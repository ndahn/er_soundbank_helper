#!/usr/bin/env python3
import sys
from typing import Any
from copy import deepcopy
import traceback
from collections import deque
from pathlib import Path
from dataclasses import dataclass
import shutil
import json
import re
from pprint import pprint


# ------------------------------------------------------------------------------------------
# Set these paths so they point to your extracted source and destination soundbanks.
SRC_BNK_DIR = "soundbanks/nr_cs_main"
DST_BNK_DIR = "soundbanks/cs_main"

# NPC sounds are usually named "c<npc-id>0<sound-id>". When moving npc sounds to the player, I 
# recommend renaming them as follows. 
# 
#     <soundtype>4<npc-id><sound-id>
# 
# This should make it easy to avoid collisions and allows you to keep track of which IDs you've 
# ported so far and from where.
# 
# The soundtype has (afaik) no meaning other than being used for calculating the event hashes, so
# you should be able to use whatever you like from this list:
# 
WWISE_IDS = {
    "c512006630": "s451206630",
    "c512006635": "s451206635",
}

# Enables writing to the destination.
ENABLE_WRITE = True

# If True, don't ask for confirmation: make reasonable assumptions and write once ready
NO_QUESTIONS = False
# ------------------------------------------------------------------------------------------


@dataclass
class Soundbank:
    bnk_dir: Path
    json: str
    id: int
    hirc: list[dict]
    idmap: dict[int, int]  # ID (or hash) to HIRC index


def calc_hash(input: str) -> int:
    # Taken from rewwise
    # https://github.com/vswarte/rewwise/blob/127d665ab5393fb7b58f1cade8e13a46f71e3972/analysis/src/fnv.rs#L6
    FNV_BASE = 2166136261
    FNV_PRIME = 16777619
    
    input_lower = input.lower()
    input_bytes = input_lower.encode()
    
    result = FNV_BASE
    for byte in input_bytes:
        result *= FNV_PRIME
        # Ensure it stays within 32-bit range
        result &= 0xFFFFFFFF
        result ^= byte
    
    return result


def load_soundbank(bnk_dir: str) -> Soundbank:
    # Resolve the path to the unpacked soundbank
    bnk_dir: Path = Path(bnk_dir)
    if not bnk_dir.is_absolute():
        bnk_dir = Path(__file__).resolve().parent / bnk_dir
    
    bnk_dir = bnk_dir.resolve()

    json_path = bnk_dir / "soundbank.json"
    with json_path.open() as f:
        bnk_json: dict = json.load(f)

    # Read the sections
    sections = bnk_json.get("sections", None)

    if not sections:
        raise ValueError("Could not find 'sections' in bnk")

    for sec in sections:
        body = sec["body"]

        if "BKHD" in body:
            bnk_id = body["BKHD"]["bank_id"]
        elif "HIRC" in body:
            hirc: list[dict] = body["HIRC"]["objects"]
        else:
            pass

    # A helper dict for mapping object IDs to HIRC indices
    idmap = {}
    for idx, obj in enumerate(hirc):
        idsec = obj["id"]
        if "Hash" in idsec:
            oid = idsec["Hash"]
            idmap[oid] = idx
        elif "String" in idsec:
            eid = idsec["String"]
            idmap[eid] = idx
            # Events are sometimes referred to by their hash, but it's not included in the json
            oid = calc_hash(eid)
            idmap[oid] = idx
        else:
            print(f"Don't know how to handle object with id {idsec}")

    return Soundbank(bnk_dir, bnk_json, bnk_id, hirc, idmap)


def print_hierarchy(debug_tree: list, prefix: str = ""):
    if not debug_tree:
        return

    for i, entry in enumerate(debug_tree):
        for node, children in entry.items():
            is_last = i == len(debug_tree) - 1
            branch = "└──" if is_last else "├──"
            print(f"{prefix}{branch} {node}")

            new_prefix = prefix + ("   " if is_last else "│  ")
            print_hierarchy(children, new_prefix)


def get_event_idx(evt_name: str, bnk: Soundbank) -> int:
    idx = bnk.idmap.get(evt_name, None)
        
    if idx is not None:
        return idx
    
    play_evt_hash = calc_hash(evt_name)
    idx = bnk.idmap.get(play_evt_hash)
    
    if idx is not None:
        return idx
    
    raise ValueError(f"Could not find index for event {evt_name}. Did you set the correct source soundbank?")


def get_id(node: dict) -> int:
    return next(iter(node["id"].values()))


def get_node_type(node: dict) -> str:
    return next(iter(node["body"].keys()))


def get_body(node: dict) -> dict:
    return node["body"][get_node_type(node)]


def get_parent_id(node: dict) -> int:
    body = get_body(node)
    return body["node_base_params"]["direct_parent_id"]


def get_user_reply(query: str, valid: str | list) -> str:
    if isinstance(valid, list):
        valid = "".join(s[0] for s in valid)
        choices = ", ".join([f"[{s[0]}]{s[1:]}" for s in valid])
    else:
        choices = "[" + "/".join(valid) + "]"

    q = f"{query} {choices} > "
    
    while True:
        reply = input(q)
        if reply in valid:
            return reply


def add_children(node: dict, *new_item_ids: int):
    body = get_body(node)

    if "children" not in body:
        # Sounds may reference nodes like "EffectCustom" as their parent, but we don't need to 
        # deal with these as they will work without explicit children
        return

    children: dict = body["children"]
    items: list = children.get("items", [])
    items.extend(new_item_ids)

    # Make sure the items are unique and sorted
    children["items"] = sorted(list(set(items)))


def transfer_wwise_main(
    src_bnk_dir: str,
    dst_bnk_dir: str,
    wwise_map: dict[str, str],
    *,
    enable_write: bool = True,
    no_questions: bool = False,
):
    # Check the IDs before we start the heavy work
    wwise_id_check = re.compile(r"[acfopsmvxbiyzegd][0-9]{9}")
    verified_wwise_map = {}
    
    for src_id, dst_id in wwise_map.items():
        # Add 0 padding to wwise IDs, otherwise the hashes will be wrong
        if len(src_id) < 10:
            src_id = src_id[0] + "0" * (10 - len(src_id)) + src_id[1:]

        if not re.fullmatch(wwise_id_check, src_id):
            raise ValueError(f"{src_id} is not a valid wwise ID")

        if len(dst_id) < 10:
            dst_id = dst_id[0] + "0" * (10 - len(dst_id)) + dst_id[1:]
        
        if not re.fullmatch(wwise_id_check, dst_id):
            raise ValueError(f"{dst_id} is not a valid wwise ID")
        
        verified_wwise_map[src_id] = dst_id

    wwise_map = verified_wwise_map

    # Load the soundbanks and prepare some lookup tables
    print("Loading source soundbank")
    src_bnk = load_soundbank(src_bnk_dir)

    print("Loading destination soundbank")
    dst_bnk = load_soundbank(dst_bnk_dir)
    
    wems = []
    transferred_indices = []

    # Now we begin
    print("Collecting sound hierarchies")
    for wwise_src, wwise_dst in wwise_map.items():
        # Find the play and stop events. The actual action comes right before the event, but
        # we could also find their ID via body/Event/actions[0] for more robustness
        play_evt_name = f"Play_{wwise_src}"
        play_evt_idx = get_event_idx(play_evt_name, src_bnk)
        stop_evt_idx = get_event_idx(f"Stop_{wwise_src}", src_bnk)

        # Indices of objects we want to transfer
        transfer_event_indices = []

        # Some events have multiple associated actions, so we can't just take the preceeding action
        stop_evt = src_bnk.hirc[stop_evt_idx]
        stop_actions = get_body(stop_evt)["actions"]
        for action_hash in stop_actions:
            action_idx = src_bnk.idmap[action_hash]
            transfer_event_indices.append(action_idx)
        transfer_event_indices.append(stop_evt_idx)

        play_evt = src_bnk.hirc[play_evt_idx]
        play_actions = get_body(play_evt)["actions"]
        for action_hash in play_actions:
            action_idx = src_bnk.idmap[action_hash]
            transfer_event_indices.append(action_idx)
        transfer_event_indices.append(play_evt_idx)

        # All the objects we want to transfer
        transfer_object_indices = deque()

        for action_hash in play_actions:
            action = src_bnk.hirc[src_bnk.idmap[action_hash]]

            # Find the container the action is triggering, then go up the hierarchy until we find
            # the ActorMixer responsible for playback
            entrypoint_id = get_body(action)["external_id"]
            if entrypoint_id in transfer_object_indices:
                continue

            entrypoint_idx = src_bnk.idmap[entrypoint_id]

            if "Event" in src_bnk.hirc[entrypoint_idx]["body"]:
                # Action references another event, let's assume ER doesn't go too crazy here
                if entrypoint_idx not in transfer_event_indices:
                    raise RuntimeError(f"Event {entrypoint_id} is referencing another event, but I don't know how to handle this (yet)!")
                continue

            # Collect the hierarchy responsible for playing the sound(s)
            new_toi, new_wems, debug_tree = collect_action_chain(src_bnk, entrypoint_id)
            
            # All children will be appended left so they appear before their parents in the 
            # transfer indices list
            transfer_object_indices.extendleft(new_toi)
            wems.extend(new_wems)

            # Go up the chain to find all the parents we need
            upchain = collect_parent_chain(src_bnk, entrypoint_id)

            play_evt_hash = calc_hash(play_evt_name)
            print(f"Parsing wwise {wwise_src} ({play_evt_hash}) resulted in the following hierarchy:")
            print(f"\nWwise {play_evt_name}")
            print_hierarchy(debug_tree)
            # pprint(debug_tree)

            print("\nThe parent chain consists of the following nodes:")
            for idx, key in enumerate(reversed(upchain)):
                node_type = get_node_type(src_bnk.hirc[src_bnk.idmap[key]])
                print(f" ⤷ {key} ({node_type})")

            # Where to insert the objects in the destination soundbank
            obj_transfer_idx = -1
            up_child_id = entrypoint_id

            # Go upwards through the parents chain and see what needs to be transferred
            for up_id in upchain:
                if up_id in dst_bnk.idmap:
                    # Once we encounter an existing node we can assume the rest of the chain is 
                    # intact. Child nodes must be inserted *before* the first existing parent. 
                    obj_transfer_idx = dst_bnk.idmap[up_id]
                    add_children(dst_bnk.hirc[dst_bnk.idmap[up_id]], up_child_id)
                    break

                up_idx = src_bnk.idmap[up_id]
                up = src_bnk.hirc[up_idx]

                if up_idx in transfer_object_indices:
                    add_children(up, up_child_id)
                    continue

                # First time we encounter upchain node, clear the children, as non-existing items 
                # will make the soundbank invalid
                get_body(up)["children"]["items"] = []
                add_children(up, up_child_id)
                
                transfer_object_indices.append(up_idx)
                up_child_id = up_id

            # collect additional items
            extras = collect_extras(src_bnk, transfer_object_indices)

            if extras:
                print("\nThe following extra items were collected:")
                for node_id in extras:
                    node = src_bnk.hirc[src_bnk.idmap[node_id]]
                    print(f" - {node_id} ({get_node_type(node)})")
                print()

            # No part of the hierarchy exists in the destination soundbank yet, place everything
            # after the first RandomSequenceContainer we find
            if obj_transfer_idx < 0:
                for idx, obj in enumerate(dst_bnk.hirc):
                    obj_type = get_node_type(obj)
                    if obj_type == "RandomSequenceContainer":
                        obj_transfer_idx = idx + 1
                        break

            # Transfer the objects
            transferred_objects = transfer_objects(src_bnk, dst_bnk, transfer_object_indices, obj_transfer_idx)
            transferred_indices.extend(transferred_objects)

            # Now we write the play and stop events into the event section
            evt_transfer_idx = -1
            for evt_idx in dst_bnk.idmap.values():
                evt = dst_bnk.hirc[evt_idx]
                evt_id = str(get_id(evt))
                if evt_id.startswith("Play_c"):
                    evt_transfer_idx = evt_idx + 1
                    break

            transferred_events = transfer_events(src_bnk, dst_bnk, transfer_event_indices, evt_transfer_idx, wwise_src, wwise_dst)
            transferred_indices.extend(transferred_events)

            transferred_extras = transfer_extras(src_bnk, dst_bnk, extras)
            transferred_indices.extend(transferred_extras)
            
            print("-" * 40 + "\n")

    print("All hierarchies collected")    
    print("\nFound the following WEMs:")
    pprint(wems)

    print("\nVerifying soundbank...")
    issues = verify_soundbank(src_bnk, dst_bnk, transferred_indices)
    if issues:
        for issue in issues:
            print(f" - {issue}")
    else:
        print(" - Looks good!")

    print()

    if not enable_write:
        print(
            f"WARNING: enable_write is False, no changes to the target soundbank or wems will be made"
        )
    else:
        if not no_questions:
            reply = get_user_reply("Write to destination?", "yn")
            
            if reply == "y":
                pass
            elif reply == "n":
                sys.exit(0)
        
        write_soundbank(src_bnk, dst_bnk, wems)
        print(f"\nDone! The following wwise play events were added to {dst_bnk.bnk_dir.name}:")
        for wwise_src, wwise_dst in wwise_map.items():
            dst_hash = calc_hash(f"Play_{wwise_dst}")
            print(f" - {wwise_src} -> {wwise_dst} ({dst_hash})")

    print("\nDon't forget to repack your soundbank!")


def collect_action_chain(bnk: Soundbank, entrypoint_id: int):
    transfer_object_indices = []
    wems = []

    visited = set()
    debug_tree = []
    entrypoint_idx = bnk.idmap[entrypoint_id]
    todo = deque([(entrypoint_idx, debug_tree)])

    # Depth first search to recover all nodes part of the wwise hierarchy
    while todo:
        node_idx, debug_parent = todo.pop()

        if node_idx in visited:
            continue

        # Will contain the highest parents in the beginning (to the left) and deeper children 
        # towards the end (right)
        transfer_object_indices.append(node_idx)
        visited.add(node_idx)

        node = bnk.hirc[node_idx]
        node_type = get_node_type(node)
        node_params = get_body(node)
        obj_id = get_id(node)

        if node_type == "Sound":
            # We found an actual sound
            wem = node_params["bank_source_data"]["media_information"]["source_id"]
            wems.append(wem)
            debug_key = f"{node_type} ({obj_id}) -> {wem}.wem"
        else:
            debug_key = f"{node_type} ({obj_id})"

        # Just for printing the hierarchy
        debug_children = []
        debug_entry = {debug_key: debug_children}
        debug_parent.append(debug_entry)

        if "children" in node_params:
            children = node_params["children"].get("items", [])

            for child_id in children:
                child_idx = bnk.idmap[child_id]
                todo.append((child_idx, debug_children))

    return transfer_object_indices, wems, debug_tree


def collect_parent_chain(bnk: Soundbank, entrypoint_id: int) -> deque:
    entrypoint = bnk.hirc[bnk.idmap[entrypoint_id]]
    parent_id = get_parent_id(entrypoint)

    upchain = deque()

    while parent_id != 0 and parent_id in bnk.idmap:
        # No early exit, we want to recover the entire upwards chain. We'll handle the 
        # parts we actually need later

        # Check for loops. No clue if that ever happens, but better be safe than sorry
        if parent_id in upchain:
            print("WARNING: parent chain seems to contain a loop")
            
            for idx in upchain:
                debug_obj = bnk.hirc[idx]
                debug_obj_id = get_id(debug_obj)
                debug_parent = get_parent_id(debug_obj)
                print(f"{debug_obj_id} -> {debug_parent}")
            
            print(f"{debug_parent} -> {parent_id}")

            reply = get_user_reply("Continue?", "yn")
            if reply == "y":
                break
            elif reply == "n":
                sys.exit(1)

        # Children before parents
        upchain.append(parent_id)
        parent = bnk.hirc[bnk.idmap[parent_id]]
        parent_id = get_parent_id(parent)

    return upchain


def collect_extras(bnk: Soundbank, transfer_object_indices: list[int]):
    extras = []

    def delve(item: Any, field: str, new_ids: set):
        if field in ["source_id", "direct_parent_id", "children"]:
            return
        
        if isinstance(item, list):
            for i, subnode in enumerate(item):
                delve(subnode, f"{field}[{i}]", new_ids)

        elif isinstance(item, dict):
            for key, val in item.items():
                delve(val, key, new_ids)

        elif isinstance(item, int):
            if item in bnk.idmap and bnk.idmap[item] not in transfer_object_indices:
                new_ids.add(item)

    for idx in transfer_object_indices:
        todo = deque([get_id(bnk.hirc[idx])])

        while todo:
            node_id = todo.pop()
            node = bnk.hirc[bnk.idmap[node_id]]
            body = get_body(node)

            new_ids = set()
            delve(body, "body", new_ids)

            for id in new_ids.difference(extras):
                todo.append(id)
                # Will contain the highest parents in the beginning (to the left) and deeper 
                # children towards the end (right)
                extras.append(id)

    return extras


def transfer_objects(src_bnk: Soundbank, dst_bnk: Soundbank, transfer_object_indices: list[int], obj_transfer_idx: int):
    transferred_indices = []

    # The first node of the HIRC is special and needs to be protected
    if obj_transfer_idx == 0:
        obj_transfer_idx += 1

    for idx in transfer_object_indices:
        obj = src_bnk.hirc[idx]
        obj_id = get_id(obj)

        if obj_id in dst_bnk.idmap:
            if no_questions:
                print(f"Skipping already existing object {obj_id} ({get_node_type(obj)})")
                reply = "s"
            else:
                obj_type = get_node_type(obj)
                reply = get_user_reply(
                    f"Object ID {obj_id} ({obj_type}) already exists in target soundbank.",
                    ["skip", "cancel", "replace"]
                )

            if reply == "s":
                # skip
                continue
            if reply == "c":
                # cancel everything
                sys.exit(-1)
            if reply == "r":
                # replace
                dst_idx = dst_bnk.idmap[obj_id]
                dst_bnk.hirc[dst_idx] = obj
                continue

        dst_bnk.hirc.insert(obj_transfer_idx, obj)
        transferred_indices.append(obj_transfer_idx)

        # Since we have inserted something, all subsequent indices will be offset
        for oid, idx in dst_bnk.idmap.items():
            if idx >= obj_transfer_idx:
                dst_bnk.idmap[oid] = idx + 1

        dst_bnk.idmap[obj_id] = obj_transfer_idx
        obj_transfer_idx += 1

    return transferred_indices


def transfer_events(src_bnk: Soundbank, dst_bnk: Soundbank, transfer_event_indices: list[int], evt_transfer_idx: int, src_wwise_id: str, dst_wwise_id: str):
    transferred_indices = []

    # The first node of the HIRC is special and needs to be protected
    if evt_transfer_idx == 0:
        evt_transfer_idx = 1

    wwise_map = {
        f"Play_{src_wwise_id}": f"Play_{dst_wwise_id}",
        f"Stop_{src_wwise_id}": f"Stop_{dst_wwise_id}",
        calc_hash(f"Play_{src_wwise_id}"): f"Play_{dst_wwise_id}",
        calc_hash(f"Stop_{src_wwise_id}"): f"Stop_{dst_wwise_id}",
    }

    for idx in transfer_event_indices:
        evt = deepcopy(src_bnk.hirc[idx])
        evt_id = get_id(evt)

        # Map to the new event ID
        if evt_id in wwise_map:
            evt["id"] = { "String": wwise_map[evt_id] }
            evt_id = wwise_map[evt_id]

        if evt_id in dst_bnk.idmap or (isinstance(evt_id, str) and calc_hash(evt_id) in dst_bnk.idmap):
            if no_questions:
                print(f"Skipping already existing event {evt_id} ({get_node_type(evt)})")
                reply = "s"
            else:
                # Getting the action type is possible but more effort, so...
                orig_eid = get_id(src_bnk.hirc[idx])
                reply = get_user_reply(
                    f"Event ID {evt_id} ({orig_eid}) already exists in target soundbank.",
                    ["skip", "cancel", "replace"]
                )

            if reply == "s":
                # skip
                continue
            if reply == "c":
                # cancel everything
                sys.exit(-1)
            if reply == "r":
                # replace
                dst_idx = dst_bnk.idmap[evt_id]
                dst_bnk.hirc[dst_idx] = evt
                continue

        # Some actions make references to other soundbanks or even their own
        if get_node_type(evt) == "Action":
            body = get_body(evt)
            params = body.get("params", None)
            if isinstance(params, dict):
                for subnode in params.values():
                    if "bank_id" in subnode:
                        orig_bnk_id = subnode["bank_id"]
                        if orig_bnk_id == src_bnk.id:
                            # NOTE: If we want to use other soundbanks we'd probably have to add them in the STID
                            subnode["bank_id"] = dst_bnk.id
                        else:
                            print(f"WARNING: action {evt_id} references external soundbank {orig_bnk_id}. "
                                    f"If this sound(bank) doesn't work, try setting this action's bank_id to {dst_bnk.id}")

        dst_bnk.hirc.insert(evt_transfer_idx, evt)
        transferred_indices.append(evt_transfer_idx)

        # Since we have inserted something, all subsequent indices will be offset
        for eid, idx in dst_bnk.idmap.items():
            if idx >= evt_transfer_idx:
                dst_bnk.idmap[eid] = idx + 1

        dst_bnk.idmap[evt_id] = evt_transfer_idx
        evt_transfer_idx += 1

    return transferred_indices


def transfer_extras(src_bnk: Soundbank, dst_bnk: Soundbank, extra_ids: list[int]):
    transferred_indices = []

    for id in extra_ids:
        if id not in src_bnk.idmap:
            continue

        if id in dst_bnk.idmap:
            continue

        idx = src_bnk.idmap[id]
        extra = src_bnk.hirc[idx]
        extra_type = get_node_type(extra)

        # Find the first object of the same type and insert the extra before that
        for insert_idx, node in enumerate(dst_bnk.hirc):
            if get_node_type(node) == extra_type:
                # The first node of the HIRC is special and needs to be protected
                if insert_idx == 0 or get_id(node) == 11895:
                    insert_idx += 1

                dst_bnk.hirc.insert(insert_idx, extra)
                transferred_indices.append(idx)

                for eid, idx in dst_bnk.idmap.items():
                    if idx >= insert_idx:
                        dst_bnk.idmap[eid] = idx + 1

                dst_bnk.idmap[id] = insert_idx
                break

    return transferred_indices


def verify_soundbank(src_bnk: Soundbank, dst_bnk: Soundbank, check_indices: list[int] = None) -> list[str]:
    discovered_ids = set([0])
    issues = []

    check_indices: set = set(check_indices or [])
    verified_indices = set()

    # We check absolutely everything!
    def delve(item: dict | list | Any, node_id: int, path: str):
        if isinstance(item, list):
            for idx, value in enumerate(item):
                delve(value, node_id, path + f"[{idx}]")

        elif isinstance(item, dict):
            for key, value in item.items():
                delve(value, node_id, path + "/" + key)

        # There's like one 5-digit hash (possibly empty string?), all others are above 10 mio
        elif isinstance(item, int) and item >= 1000000:
            if path.endswith("source_id"):
                # WEMs won't appear in the HIRC
                pass

            elif path.endswith("bank_id"):
                if item != dst_bnk.id:
                    # Not sure if this can be an issue
                    issues.append(f"{node_id}:reference to external soundbank {item}")
            
            elif path.endswith("id/Hash"):
                if item in discovered_ids:
                    issues.append(f"{node_id}: has duplicates")

            elif path.endswith("id/String"):
                if calc_hash(item) in discovered_ids:
                    issues.append(f"{node_id}: has duplicates")
            
            elif path.endswith("direct_parent_id"):
                if item in discovered_ids:
                    issues.append(f"{node_id}: is defined after its parent {item}")

            elif item not in discovered_ids:
                exists = (item in src_bnk.hirc)
                if exists:
                    issues.append(f"{node_id}: {path}: reference {item} was not transferred")
                else:
                    issues.append(f"{node_id}: {path}: reference {item} does not exist (probably okay?)")

    for idx, node in enumerate(dst_bnk.hirc):
        node_id = get_id(node)

        if node_id in discovered_ids:
            issues.append(f"{node_id}: node has been defined before")
            continue

        discovered_ids.add(node_id)
        if idx not in check_indices:
            continue

        delve(get_body(node), node_id, "")

        # References to other objects will always be by hash
        if isinstance(node_id, str):
            node_id = calc_hash(node_id)

        verified_indices.add(idx)

    if check_indices and len(verified_indices) < len(check_indices):
        issues.append(f"Expected nodes not found: {[check_indices.difference(verified_indices)]}")

    return issues


def write_soundbank(src_bnk: Soundbank, dst_bnk: Soundbank, wems: list[int]):
    print(f"Writing destination soundbank ({len(dst_bnk.hirc)} nodes)")
    # Replace the original hirc in the destination soundbank
    dst_sections = dst_bnk.json["sections"]
    for idx, sec in enumerate(dst_sections):
        if "HIRC" in sec["body"]:
            sec["body"]["HIRC"]["objects"] = dst_bnk.hirc
            break

    bnk_json_path = dst_bnk.bnk_dir / "soundbank.json"

    backup = dst_bnk.bnk_dir.name.rsplit(".", maxsplit=1)[0] + "_backup.json"
    shutil.move(bnk_json_path, dst_bnk.bnk_dir.parent / backup)
    with bnk_json_path.open("w") as f:
        json.dump(dst_bnk.json, f, indent=2)

    print(f"Copying {len(wems)} wems")
    for wem in wems:
        wem_name = f"{wem}.wem"
        # Copy even if the file exists already in case something went wrong before
        (dst_bnk.bnk_dir / wem_name).unlink(missing_ok=True)
        shutil.copy(src_bnk.bnk_dir / wem_name, dst_bnk.bnk_dir / wem_name)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        src_bnk_dir = SRC_BNK_DIR
        dst_bnk_dir = DST_BNK_DIR
        wwise_ids = WWISE_IDS
        enable_write = ENABLE_WRITE
        no_questions = NO_QUESTIONS
    else:
        import argparse

        parser = argparse.ArgumentParser(
            description="A nifty tool for transfering wwise sounds between From software soundbanks."
        )

        parser.add_argument("src_bnk", type=str, help="The source soundbank folder")
        parser.add_argument(
            "dst_bnk", type=str, help="The destination soundbank folder"
        )
        parser.add_argument(
            "sound_ids",
            type=str,
            nargs="+",
            help="Specify as '<type><source-id>:=<type><destination-id>', e.g. 'c123456789:=s0987654321' (or just <type><id> if you want to copy as is)",
        )
        parser.add_argument(
            "--disable_write",
            action="store_true",
            help="If True, no changes to the destination soundbank will be made",
        )
        parser.add_argument(
            "--no_questions",
            action="store_true",
            help="Assume sensible defaults instead of asking for confirmations",
        )

        args = parser.parse_args()

        if args.help:
            parser.print_help()
            sys.exit(1)

        src_bnk_dir = args.src_bnk
        dst_bnk_dir = args.dst_bnk
        enable_write = not args.disable_write
        no_questions = args.no_questions

        wwise_ids = {}
        wwise_id_check = re.compile(r"[a-z][0-9]+")

        for s in args.sound_ids:
            if ":=" in s:
                src_id, dst_id = s.split(":=")
            else:
                src_id = dst_id = s

            if not (re.fullmatch(wwise_id_check, src_id) and re.fullmatch(wwise_id_check, dst_id)):
                raise ValueError(f"Invalid sound ID specification {s}")
            
            wwise_ids[src_id] = dst_id

    try:
        transfer_wwise_main(
            src_bnk_dir,
            dst_bnk_dir,
            wwise_ids,
            enable_write=enable_write,
            no_questions=no_questions,
        )
    except Exception as e:
        if hasattr(sys, "gettrace") and sys.gettrace() is not None:
            # Debugger is active, let the debugger handle it
            raise

        # In case we are run from a temporary terminal, otherwise we won't see what's wrong
        print(traceback.format_exc())
    
    input("Press enter to exit...")
