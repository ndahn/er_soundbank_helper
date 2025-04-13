#!/usr/bin/env python3
import sys
from copy import deepcopy
import traceback
from collections import deque
from pathlib import Path
import shutil
import json
import re
from pprint import pprint

"""
# 2010
c201005002

# 2500
c250006503

# 4520
c452006107
c452006106
c452006102
c452005011
c452005008
c452005010
c452007001

# 4770
c477008001
c477001000
c477006500
c477008003
c477005006

# 5120
c512006630
c512006635
"""

SRC_BNK_DIR = "../cs_c4520"
DST_BNK_DIR = "../cs_main"

# NPC sounds are usually named <npc-id>0<sound-id>. When moving npc sounds to the player, I 
# recommend renaming them as follows. 
#
#     4<npc-id><sound-id>
#
# This should make it easy to avoid collisions and allows you to keep track which IDs you've 
# ported so far and from where.
WWISE_IDS = {
    "c452005011": "s445205011",
    "c452006107": "s445206107",
}
ENABLE_WRITE = True

# If True, don't ask for confirmation: skip existing entries in the destination and write once ready
NO_QUESTIONS = False


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


def load_indices(
    bnk: dict,
) -> tuple[list[dict], dict[int, int]]:
    sections = bnk.get("sections", None)

    if not sections:
        raise ValueError("Could not find 'sections' in bnk")

    for sec in sections:
        if "HIRC" in sec["body"]:
            hirc: list[dict] = sec["body"]["HIRC"]["objects"]
            break
    else:
        raise ValueError("Could not find HIRC in bnk")

    id_map = {}
    for idx, obj in enumerate(hirc):
        idsec = obj["id"]
        if "Hash" in idsec:
            oid = idsec["Hash"]
            id_map[oid] = idx
        elif "String" in idsec:
            eid = idsec["String"]
            id_map[eid] = idx
            # Events are sometimes referred to by their hash, but it's not included in the json
            oid = calc_hash(eid)
            id_map[oid] = idx
        else:
            print(f"Don't know how to handle object with id {idsec}")

    return hirc, id_map


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


def get_event_idx(evt_name: str, id_map: dict[int, int]) -> int:
    idx = id_map.get(evt_name, None)
        
    if idx is not None:
        return idx
    
    play_evt_hash = calc_hash(evt_name)
    idx = id_map.get(play_evt_hash)
    
    if idx is not None:
        return idx
    
    raise ValueError(f"Could not find index for event {evt_name}")


def get_id(node: dict) -> int:
    return next(iter(node["id"].values()))


def get_node_type(node: dict) -> str:
    return next(iter(node["body"].keys()))


def get_body(node: dict) -> dict:
    return node["body"][get_node_type(node)]


def get_parent_id(node: dict) -> int:
    body = get_body(node)
    return body["node_base_params"]["direct_parent_id"]


def add_children(node: dict, *new_items: int):
    body = get_body(node)

    if "children" not in body:
        # Sounds may reference nodes like "EffectCustom" as their parent, but we don't need to 
        # deal with these as they will work without explicit children
        return

    children: dict = body["children"]
    items: list = children.get("items", [])
    items.extend(new_items)

    # Make sure the items are unique and sorted
    children["items"] = sorted(list(set(items)))


def main(
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
    src_bnk_dir: Path = Path(src_bnk_dir)
    dst_bnk_dir: Path = Path(dst_bnk_dir)

    if not src_bnk_dir.is_absolute():
        src_bnk_dir = Path(__file__).resolve().parent / src_bnk_dir

    if not dst_bnk_dir.is_absolute():
        dst_bnk_dir = Path(__file__).resolve().parent / dst_bnk_dir

    src_bnk_dir = src_bnk_dir.resolve()
    dst_bnk_dir = dst_bnk_dir.resolve()

    print("Loading source soundbank")
    src_json = src_bnk_dir / "soundbank.json"
    with src_json.open() as f:
        src_bnk: dict = json.load(f)

    print("Loading destination soundbank")
    dst_json = dst_bnk_dir / "soundbank.json"
    with dst_json.open() as f:
        dst_bnk: dict = json.load(f)

    src_hirc, src_idmap = load_indices(src_bnk)
    dst_hirc, dst_idmap = load_indices(dst_bnk)
    wems = []

    # Now we begin
    print("Collecting sound hierarchies")
    for wwise_src, wwise_dst in wwise_map.items():
        # Find the play and stop events. The actual action comes right before the event, but
        # we could also find their ID via body/Event/actions[0] for more robustness
        play_evt_hash = f"Play_{wwise_src}"
        play_evt_idx = get_event_idx(play_evt_hash, src_idmap)
        stop_evt_idx = get_event_idx(f"Stop_{wwise_src}", src_idmap)

        # Indices of objects we want to transfer
        transfer_event_indices = []

        # Some events have multiple associated actions, so we can't just take the preceeding action
        stop_evt = src_hirc[stop_evt_idx]
        stop_actions = get_body(stop_evt)["actions"]
        for action_hash in stop_actions:
            action_idx = src_idmap[action_hash]
            transfer_event_indices.append(action_idx)
        transfer_event_indices.append(stop_evt_idx)

        play_evt = src_hirc[play_evt_idx]
        play_actions = get_body(play_evt)["actions"]
        for action_hash in play_actions:
            action_idx = src_idmap[action_hash]
            transfer_event_indices.append(action_idx)
        transfer_event_indices.append(play_evt_idx)

        # All the objects we want to transfer
        transfer_object_indices = deque()

        for action_hash in play_actions:
            action = src_hirc[src_idmap[action_hash]]

            # Find the container the action is triggering, then go up the hierarchy until we find
            # the ActorMixer responsible for playback
            parent_id = get_body(action)["external_id"]
            if parent_id in transfer_object_indices:
                continue

            root_idx = src_idmap[parent_id]

            if "Event" in src_hirc[root_idx]["body"]:
                # Action references another event, let's assume ER doesn't go too crazy here
                if root_idx not in transfer_event_indices:
                    raise RuntimeError(f"Event {parent_id} is referencing another event, but I don't know how to handle this (yet)!")
                continue

            visited = set()
            debug_tree = []
            todo = deque([(root_idx, debug_tree)])

            # Depth first search to recover all nodes part of the wwise hierarchy
            while todo:
                node_idx, debug_parent = todo.pop()

                if node_idx in visited:
                    return

                # All children will be appended left so they appear before their
                # parents in the transfer indices list
                transfer_object_indices.appendleft(node_idx)
                visited.add(node_idx)

                node = src_hirc[node_idx]
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
                        child_idx = src_idmap[child_id]
                        todo.append((child_idx, debug_children))

            # Go up the chain to find all the parents we need
            entrypoint = src_hirc[root_idx]
            parent_id = get_parent_id(entrypoint)

            upchain = deque()

            while parent_id != 0:
                if parent_id in dst_idmap:
                    # Parent chain is already in place, no need to transfer anything
                    break

                # Check for loops. No clue if that ever happens, but better be safe than sorry
                if parent_id in upchain:
                    print("WARNING: parent chain seems to contain a loop")
                    
                    for idx in upchain:
                        debug_obj = src_hirc[idx]
                        debug_obj_id = get_id(debug_obj)
                        debug_parent = get_parent_id(debug_obj)
                        print(f"{debug_obj_id} -> {debug_parent}")
                    
                    print(f"{debug_parent} -> {parent_id}")

                    while True:
                        reply = input("Continue? [y/n] > ")

                        if reply == "y":
                            break

                        if reply == "n":
                            sys.exit(1)

                    break

                upchain.appendleft(parent_id)
                parent = src_hirc[src_idmap[parent_id]]
                parent_id = get_parent_id(parent)

            print(f"Parsing wwise {wwise_src} resulted in the following hierarchy:")
            print(f"\nWwise {wwise_src} ({play_evt_hash})")
            print_hierarchy(debug_tree)
            print()
            # pprint(debug_tree)

            print("The following wems were collected:")
            pprint(wems)
            print()

            print("The parent chain consists of the following nodes:")
            upchain_debug = [f"{key} ({get_node_type(src_hirc[src_idmap[key]])})" for key in upchain]
            pprint(upchain_debug)
            print("----------\n")

            # Where to insert the objects in the destination soundbank
            obj_transfer_idx = -1

            # Go through the parents chain and see what needs to be transferred
            for up_id in reversed(upchain):
                if up_id in dst_idmap:
                    # Must be inserted *before* the parent
                    obj_transfer_idx = dst_idmap[up_id]
                    break

                up_idx = src_idmap[up_id]
                if up_idx in transfer_object_indices:
                    # TODO add children
                    continue

                transfer_object_indices.append(up_idx)
                up = src_hirc[up_idx]
                up_parent_id = get_parent_id(up)

                if up_parent_id != 0:
                    if up_parent_id in dst_idmap:
                        # parent is already in the destination soundbank
                        up_parent = dst_hirc[dst_idmap[up_parent_id]]
                    else:
                        # parent still has to be transferred
                        up_parent = deepcopy(src_hirc[src_idmap[up_parent_id]])
                        # TODO tis a copy, we have to add it somewhere
                        children = get_body(up_parent)["children"]
                        children["items"] = []

                    add_children(up_parent, up_id)

            # No part of the hierarchy exists in the destination soundbank yet, place everything
            # after the first RandomSequenceContainer we find
            if obj_transfer_idx < 0:
                for idx, obj in enumerate(dst_hirc):
                    obj_type = get_node_type(obj)
                    if obj_type == "RandomSequenceContainer":
                        obj_transfer_idx = idx + 1
                        break

            # Shouldn't be required if everything works as expected
            unique_indices = set(transfer_object_indices)
            if len(unique_indices) != len(transfer_object_indices):
                duplicates = [
                    idx for idx in unique_indices if transfer_object_indices.count(idx) > 1
                ]
                print(f"WARNING: found duplicate indices {duplicates}")

                # The power of ordered dicts, right at my fingertips *_*
                transfer_object_indices = list(dict.fromkeys(transfer_object_indices))

            # Transfer the objects
            for idx in transfer_object_indices:
                obj = src_hirc[idx]
                obj_id = get_id(obj)

                if obj_id in dst_idmap:
                    if no_questions:
                        print(f"Skipping already existing object {obj_id}")
                        reply = "s"
                    else:
                        while True:
                            obj_type = get_node_type(obj)
                            reply = input(
                                f"Object ID {obj_id} ({obj_type}) already exists in target soundbank. "
                                "[s]kip, [c]ancel, [r]eplace? > "
                            )

                            if reply in "scr":
                                break

                    if reply == "s":
                        # skip
                        continue
                    if reply == "c":
                        # cancel everything
                        sys.exit(-1)
                    if reply == "r":
                        # replace
                        dst_idx = dst_idmap[obj_id]
                        dst_hirc[dst_idx] = obj
                        continue

                dst_hirc.insert(obj_transfer_idx, obj)

                # Since we have inserted something, all subsequent indices will be offset
                for oid, idx in dst_idmap.items():
                    if idx >= obj_transfer_idx:
                        dst_idmap[oid] = idx + 1

                dst_idmap[obj_id] = obj_transfer_idx
                obj_transfer_idx += 1

            # Now we write the play and stop events into the event section
            evt_transfer_idx = -1
            for evt_idx in dst_idmap.values():
                evt = dst_hirc[evt_idx]
                evt_id = str(get_id(evt))
                if evt_id.startswith("Play_c"):
                    evt_transfer_idx = evt_idx + 1
                    break

            for idx in transfer_event_indices:
                evt = deepcopy(src_hirc[idx])

                if idx == play_evt_idx:
                    evt["id"] = { "Hash": calc_hash(f"Play_{wwise_dst}") }
                elif idx == stop_evt_idx:
                    evt["id"] = { "Hash": calc_hash(f"Stop_{wwise_dst}") }

                evt_id = get_id(evt)

                if evt_id in dst_idmap:
                    if no_questions:
                        print(f"Skipping already existing event {evt_id}")
                        reply = "s"
                    else:
                        while True:
                            # Getting the action type is possible but more effort, so...
                            orig_eid = get_id(src_hirc[idx])
                            reply = input(
                                f"Event ID {evt_id} ({orig_eid}) already exists in target soundbank. "
                                "[s]kip, [c]ancel, [r]replace? > "
                            )

                            if reply in "scr":
                                break

                    if reply == "s":
                        # skip
                        continue
                    if reply == "c":
                        # cancel everything
                        sys.exit(-1)
                    if reply == "r":
                        # replace
                        dst_idx = dst_idmap[evt_id]
                        dst_hirc[dst_idx] = evt
                        continue

                dst_hirc.insert(evt_transfer_idx, evt)

                # Since we have inserted something, all subsequent indices will be offset
                for eid, idx in dst_idmap.items():
                    if idx >= evt_transfer_idx:
                        dst_idmap[eid] = idx + 1

                dst_idmap[evt_id] = evt_transfer_idx
                evt_transfer_idx += 1

    print("All hierarchies collected")

    if not enable_write:
        print(
            f"-> enable_write is False, no changes to the target soundbank or wems will be made"
        )
    else:
        if not no_questions:
            reply = ""
            while not reply or reply not in "yn":
                reply = input("Write to destination? [y/n] > ")

            if reply == "y":
                pass
            else:
                sys.exit(0)

        print(f"Writing destination soundbank ({len(dst_hirc)} nodes)")
        # Replace the original hirc in the destination soundbank
        dst_sections = dst_bnk["sections"]
        for idx, sec in enumerate(dst_sections):
            if "HIRC" in sec["body"]:
                sec["body"]["HIRC"]["objects"] = dst_hirc
                break

        backup = dst_bnk_dir.name.rsplit(".", maxsplit=1)[0] + "_backup.json"
        shutil.move(dst_json, dst_bnk_dir.parent / backup)
        with dst_json.open("w") as f:
            json.dump(dst_bnk, f, indent=2)

        print(f"Copying {len(wems)} wems")
        for wem in wems:
            wem_name = f"{wem}.wem"
            if (dst_bnk_dir / wem_name).is_file():
                print(f"wem {wem_name} already exists, skipping")
            else:
                shutil.copy(src_bnk_dir / wem_name, dst_bnk_dir / wem_name)

    print("\nDone! The following wwise play events were registered:")
    for wwise_src, wwise_dst in wwise_map.items():
        print(f" - {wwise_src} -> {wwise_dst} ({calc_hash(wwise_dst)})")

    input("\nNext, repack your target soundbank. Press Enter to exit...")


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
        no_questions = parser.no_questions

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
        main(
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
        input("\nPress enter to exit")
