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


def main(
    src_bnk_dir: str,
    dst_bnk_dir: str,
    wwise_map: dict[str, str],
    *,
    enable_write: bool = True,
    no_questions: bool = False,
):
    wwise_id_check = re.compile(r"[acfopsmvxbiyzegd][0-9]{9}")

    if not all(re.fullmatch(wwise_id_check, key) for key in wwise_map.keys()):
        raise ValueError("All source wwise IDs must follow the pattern <SoundType><9-digit-ID>")
    
    if not all(re.fullmatch(wwise_id_check, val) for val in wwise_map.values()):
        raise ValueError("All destination wwise IDs must follow the pattern <SoundType><9-digit-ID>")

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

    print("Collecting sound hierarchies")
    for wwise_src, wwise_dst in wwise_map.items():
        # Find the play and stop events. The actual action comes right before the event, but
        # we could also find their ID via body/Event/actions[0] for more robustness
        play_evt_idx = get_event_idx(f"Play_{wwise_src}", src_idmap)
        stop_evt_idx = get_event_idx(f"Stop_{wwise_src}", src_idmap)

        # Indices of objects we want to transfer
        transfer_event_indices = []

        # Some events have multiple associated actions, so we can't just take the preceeding action
        stop_evt = src_hirc[stop_evt_idx]
        stop_actions = stop_evt["body"]["Event"]["actions"]
        for action_hash in stop_actions:
            action_idx = src_idmap[action_hash]
            transfer_event_indices.append(action_idx)
        transfer_event_indices.append(stop_evt_idx)

        play_evt = src_hirc[play_evt_idx]
        play_actions = play_evt["body"]["Event"]["actions"]
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
            parent_id = action["body"]["Action"]["external_id"]
            if parent_id in transfer_object_indices:
                continue

            root_idx = src_idmap[parent_id]

            if "Event" in src_hirc[root_idx]["body"]:
                # Action references another event, let's assume ER doesn't go too crazy here
                if root_idx not in transfer_event_indices:
                    raise RuntimeError("Don't know how to handle references to foreign events (yet)!")
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
                node_type = next(iter(node["body"].keys()))
                node_params = node["body"][node_type]
                oid = next(iter(node["id"].values()))

                if node_type == "Sound":
                    # We found an actual sound
                    wem = node_params["bank_source_data"]["media_information"]["source_id"]
                    wems.append(wem)
                    debug_key = f"{node_type} ({oid}) -> {wem}.wem"
                else:
                    debug_key = f"{node_type} ({oid})"

                # Just for printing the hierarchy
                debug_children = []
                debug_entry = {debug_key: debug_children}
                debug_parent.append(debug_entry)

                if "children" in node_params:
                    children = node_params["children"].get("items", [])

                    for child_id in children:
                        child_idx = src_idmap[child_id]
                        todo.append((child_idx, debug_children))

            # Go up to find the ActorMixer the root belongs to
            actor_mixer_id = None
            root = src_hirc[root_idx]
            root_id = next(iter(root["id"].values()))
            root_params = next(iter(root["body"].values()))
            parent_id = root_params["node_base_params"]["direct_parent_id"]

            while True:
                parent_idx = src_idmap[parent_id]

                parent = src_hirc[parent_idx]
                parent_type = next(iter(parent["body"].keys()))
                parent_params = parent["body"][parent_type]
                oid = next(iter(parent["id"].values()))

                # The ActorMixer is where our hierarchy will be included from
                if parent_type == "ActorMixer":
                    actor_mixer_id = oid
                    break

                # ActorMixer should be the direct parent, but you never know...
                transfer_object_indices.append(parent_idx)
                parent_id = parent_params["node_base_params"]["direct_parent_id"]

            if actor_mixer_id is None:
                raise ValueError("ActorMixer could not be found")

            print(f"Parsing wwise {wwise_src} resulted in the following hierarchy:")
            print(f"\nWwise {wwise_src}")
            print_hierarchy(debug_tree)
            print()
            # pprint(debug_tree)

            print("The following wems were collected:")
            pprint(wems)
            print()

            # Check if the ActorMixer already exists in the destination soundbank
            if actor_mixer_id in dst_idmap:
                obj_transfer_idx = dst_idmap[actor_mixer_id]
                dst_actor_mixer = dst_hirc[obj_transfer_idx]

                print(
                    f"Inserting {len(transfer_object_indices)} nodes before ActorMixer {actor_mixer_id}"
                )
            else:
                # ActorMixer does not exist yet, copy it below the first SC
                actor_mixer_idx = src_idmap[actor_mixer_id]
                dst_actor_mixer = src_hirc[
                    actor_mixer_idx
                ]  # not a copy, but should be okay
                dst_actor_mixer["body"]["ActorMixer"]["children"]["items"] = []
                transfer_object_indices.append(src_idmap[actor_mixer_id])

                obj_transfer_idx = -1
                for idx, obj in enumerate(dst_hirc):
                    obj_type = next(iter(obj["body"].keys()))
                    if obj_type == "RandomSequenceContainer":
                        obj_transfer_idx = idx + 1
                        break

                print(
                    f"Copying ActorMixer {actor_mixer_id} and {len(transfer_object_indices) - 1} nodes"
                )

            # Add the hierarchy we're about to insert to the destination's ActorMixer's children
            dst_am_children: list = dst_actor_mixer["body"]["ActorMixer"]["children"]
            dst_am_children_items = dst_am_children.setdefault("items", [])
            dst_am_children_items.append(root_id)
            dst_am_children_items.sort()

            # Shouldn't be required if everything works as expected
            unique_indices = set(transfer_object_indices)
            if len(unique_indices) != len(transfer_object_indices):
                duplicates = [
                    idx for idx in unique_indices if transfer_object_indices.count(idx) > 1
                ]
                print(f"Warning: found duplicate indices {duplicates}")

                # The power of ordered dicts, right at my fingertips *_*
                transfer_object_indices = list(dict.fromkeys(transfer_object_indices))

            # Transfer the objects
            for idx in transfer_object_indices:
                obj = src_hirc[idx]
                oid = next(iter(obj["id"].values()))

                if oid in dst_idmap:
                    if no_questions:
                        print(f"Skipping already existing object {oid}")
                        reply = "s"
                    else:
                        while True:
                            obj_type = next(iter(obj["body"].keys()))
                            reply = input(
                                f"Object ID {oid} ({obj_type}) already exists in target soundbank. "
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
                        dst_idx = dst_idmap[oid]
                        dst_hirc[dst_idx] = obj
                        continue

                dst_hirc.insert(obj_transfer_idx, obj)

                # Since we have inserted something, all subsequent indices will be offset
                for oid, idx in dst_idmap.items():
                    if idx >= obj_transfer_idx:
                        dst_idmap[oid] = idx + 1

                obj_transfer_idx += 1

            # Now we write the play and stop events into the event section
            evt_transfer_idx = -1
            for evt_idx in dst_idmap.values():
                evt = dst_hirc[evt_idx]
                evt_id = str(next(iter(evt["id"].values())))
                if evt_id.startswith("Play_c"):
                    evt_transfer_idx = evt_idx + 1
                    break

            for idx in transfer_event_indices:
                evt = deepcopy(src_hirc[idx])

                if idx == play_evt_idx:
                    evt["id"] = { "Hash": calc_hash(f"Play_{wwise_dst}") }
                elif idx == stop_evt_idx:
                    evt["id"] = { "Hash": calc_hash(f"Stop_{wwise_dst}") }

                eid = next(iter(evt["id"].values()))

                if eid in dst_idmap:
                    if no_questions:
                        print(f"Skipping already existing event {eid}")
                        reply = "s"
                    else:
                        while True:
                            # Getting the action type is possible but more effort, so...
                            orig_eid = next(iter(src_hirc[idx]["id"].values()))
                            reply = input(
                                f"Event ID {eid} ({orig_eid}) already exists in target soundbank. "
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
                        dst_idx = dst_idmap[eid]
                        dst_hirc[dst_idx] = evt
                        continue

                dst_hirc.insert(evt_transfer_idx, evt)

                # Since we have inserted something, all subsequent indices will be offset
                for eid, idx in dst_idmap.items():
                    if idx >= evt_transfer_idx:
                        dst_idmap[eid] = idx + 1

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
            type=int,
            nargs="+",
            help="The wwise sound IDs you wish to transfer",
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
        wwise_ids = args.sound_ids
        enable_write = not args.disable_write
        no_questions = parser.no_questions

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
