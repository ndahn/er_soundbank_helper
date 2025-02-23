#!/usr/bin/env python3
import sys
import traceback
from pathlib import Path
import shutil
import json
from pprint import pprint


SRC_BNK_DIR = "cs_c4770"
DST_BNK_DIR = "cs_main"
WWISE_IDS = [
    477008001,
]
ENABLE_WRITE = False


def load_indices(
    bnk: dict,
) -> tuple[list[dict], dict[int, int], dict[str, int]]:
    sections = bnk.get("sections", None)

    if not sections:
        raise ValueError("Could not find 'sections' in bnk")

    for sec in sections:
        if "HIRC" in sec["body"]:
            hirc: list[dict] = sec["body"]["HIRC"]["objects"]
            break
    else:
        raise ValueError("Could not find HIRC in bnk")

    objects = {}
    events = {}
    for idx, obj in enumerate(hirc):
        idsec = obj["id"]
        if "Hash" in idsec:
            oid = idsec["Hash"]
            objects[oid] = idx
        elif "String" in idsec:
            eid = idsec["String"]
            events[eid] = idx
        else:
            print(f"Don't know how to handle object with id {idsec}")

    return hirc, objects, events


def main(
    src_bnk_dir: str,
    dst_bnk_dir: str,
    wwise_ids: list[int],
    enable_write: bool = True,
):
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

    src_hirc, src_oid_to_idx, src_eid_to_idx = load_indices(src_bnk)
    dst_hirc, dst_oid_to_idx, _ = load_indices(dst_bnk)
    wems = []

    print("Collecting sound hierarchies")
    for wwise in wwise_ids:
        # Just for debugging
        debug_tree = {}

        # Find the play and stop events. The actual action comes right before the event, but
        # we could also find their ID via body/Event/actions[0] for more robustness
        play_evt_idx = src_eid_to_idx.get(f"Play_c{wwise}", None)
        if play_evt_idx is None:
            raise ValueError(f"Could not find wwise ID {wwise} in source soundbank")

        stop_evt_idx = src_eid_to_idx[f"Stop_c{wwise}"]

        # Add the play/stop events and actions
        transfer_object_indices = [
            play_evt_idx - 1,
            play_evt_idx,
            stop_evt_idx - 1,
            stop_evt_idx,
        ]

        # Find the container the action is triggering, then go up the hierarchy until we find
        # the ActorMixer responsible for playback
        parent_id = src_hirc[play_evt_idx - 1]["body"]["Action"]["external_id"]
        parent_idx = src_oid_to_idx[parent_id]
        actor_mixer_id = None
        snd_sc_root_id = None

        while True:
            node = src_hirc[parent_idx]
            node_type = next(iter(node["body"].keys()))
            node_params = node["body"][node_type]
            oid = next(iter(node["id"].values()))

            # The ActorMixer is where our hierarchy will be included from
            if node_type == "ActorMixer":
                actor_mixer_id = oid
                break

            if node_type == "Sound":
                wem = node_params["bank_source_data"]["media_information"]["source_id"]
                wems.append(wem)

            if "children" in node_params:
                children = node_params["children"].get("items", [])
                debug_children = debug_tree.setdefault("children", [])
                
                for child_id in children:
                    child_idx = src_oid_to_idx[child_id]
                    transfer_object_indices.append(child_idx)

                    # Assume that all sound objects will be leafs
                    child = src_hirc[child_idx]
                    child_type = next(iter(child["body"].keys()))
                    if child_type == "Sound":
                        wem = child["body"]["Sound"]["bank_source_data"]["media_information"]["source_id"]
                        wems.append(wem)

                    debug_children.append(f"{child_type} ({child_id})")
                
            transfer_object_indices.append(parent_idx)
            
            debug_tree = {f"{node_type} ({oid})": debug_tree}

            # Go up the hierarchy until we find an ActorMixer
            snd_sc_root_id = parent_id
            parent_id = node_params["node_base_params"]["direct_parent_id"]
            parent_idx = src_oid_to_idx[parent_id]

        if actor_mixer_id is None:
            raise ValueError("ActorMixer could not be found")

        print(f"Parsing wwise {wwise} resulted in the following hierarchy:")
        pprint(debug_tree)

        print("The following wems were collected:")
        pprint(wems)

        # Check if the ActorMixer already exists in the destination soundbank
        if actor_mixer_id in dst_oid_to_idx:
            transfer_idx = dst_oid_to_idx[actor_mixer_id]
            dst_actor_mixer = dst_hirc[transfer_idx]

            print(
                f"Inserting {len(transfer_object_indices)} nodes before actor mixer {actor_mixer_id}"
            )
        else:
            # ActorMixer does not exist yet, copy it below the first SC
            actor_mixer_idx = src_oid_to_idx[actor_mixer_id]
            dst_actor_mixer = src_hirc[actor_mixer_idx]  # not a copy, but should be okay
            dst_actor_mixer["body"]["ActorMixer"]["children"]["items"] = []
            transfer_object_indices.append(src_oid_to_idx[actor_mixer_id])

            transfer_idx = -1
            for idx, obj in enumerate(dst_hirc):
                obj_type = next(iter(obj["body"].keys()))
                if obj_type == "RandomSequenceContainer":
                    transfer_idx = idx + 1
                    break
            
            print(f"Copying ActorMixer {actor_mixer_id} and {len(transfer_object_indices) - 1} nodes")

        for idx in transfer_object_indices:
            obj = src_hirc[idx]
            oid = next(iter(obj["id"].values()))

            if oid in dst_oid_to_idx:
                while True:
                    reply = input(
                        f"Object ID {oid} already exists in target soundbank. "
                        "[s]kip, [c]ancel, [w]rite? > "
                    )
                    if reply == "s":
                        # skip
                        continue
                    if reply == "c":
                        # cancel everything
                        sys.exit(-1)
                    if reply == "w":
                        # write anyways
                        break

            dst_hirc.insert(transfer_idx, obj)
            
            # Since we have inserted something, all subsequent indices will be offset
            for oid, idx in dst_oid_to_idx.items():
                if idx >= transfer_idx:
                    dst_oid_to_idx[oid] = idx + 1

            transfer_idx += 1

        # Add the inserted hierarchy to the ActorMixer in the destination
        dst_am_children: list = dst_actor_mixer["body"]["ActorMixer"]["children"]
        dst_am_children_items = dst_am_children.setdefault("items", [])
        dst_am_children_items.append(snd_sc_root_id)
        dst_am_children_items.sort()

    print("All hierarchies collected")

    if not enable_write:
        print(
            f"enable_write is False, no changes to the target soundbank or wems will be made"
        )
    else:
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

    input("\nDone! Press Enter to exit")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        src_bnk_dir = SRC_BNK_DIR
        dst_bnk_dir = DST_BNK_DIR
        wwise_ids = WWISE_IDS
        enable_write = ENABLE_WRITE
    else:
        import argparse

        parser = argparse.ArgumentParser(description="A nifty tool for transfering wwise sounds between From software soundbanks.")

        parser.add_argument("src_bnk", type=str, help="The source soundbank folder")
        parser.add_argument("dst_bnk", type=str, help="The destination soundbank folder")
        parser.add_argument("sound_ids", type=int, nargs="+", help="The wwise sound IDs you wish to transfer")
        parser.add_argument("--disable_write", action="store_true", help="If True, no changes to the destination soundbank will be made")

        args = parser.parse_args()

        if args.help:
            parser.print_help()
            sys.exit(1)

        src_bnk_dir = args.src_bnk
        dst_bnk_dir = args.dst_bnk
        wwise_ids = args.sound_ids
        enable_write = not args.disable_write

    try:
        main(src_bnk_dir, dst_bnk_dir, wwise_ids, enable_write)
    except Exception as e:
        if hasattr(sys, "gettrace") and sys.gettrace() is not None:
            # Debugger is active, let the debugger handle it
            raise

        # In case we are run from a temporary terminal, otherwise we won't see what's wrong
        print(traceback.format_exc())
        input("\nPress enter to exit")
