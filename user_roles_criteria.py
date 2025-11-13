#!/usr/bin/env python3

import json
import os
import argparse
import uuid
from dataclasses import dataclass, asdict
from typing import List, Dict

DATA_FILE = "roles_criteria.json"
DEFAULT_ROLES = ["donor", "recipient", "volunteer", "admin"]

@dataclass
class Criteria:
    id: str
    text: str
    mandatory: bool = True

def _default_data():
    return {r: [] for r in DEFAULT_ROLES}

class RolesManager:
    def __init__(self, path: str = DATA_FILE):
        self.path = path
        if not os.path.exists(self.path):
            self._write(_default_data())
        self._data = self._read()

    def _read(self) -> Dict[str, List[Dict]]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: Dict[str, List[Dict]]):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._data = data

    def list_roles(self) -> List[str]:
        return list(self._data.keys())

    def ensure_role(self, role: str):
        if role not in self._data:
            self._data[role] = []
            self._write(self._data)

    def add_criteria(self, role: str, text: str, mandatory: bool = True) -> Criteria:
        self.ensure_role(role)
        c = Criteria(id=str(uuid.uuid4()), text=text.strip(), mandatory=bool(mandatory))
        self._data[role].append(asdict(c))
        self._write(self._data)
        return c

    def list_criteria(self, role: str) -> List[Criteria]:
        self.ensure_role(role)
        return [Criteria(**c) for c in self._data.get(role, [])]

    def remove_criteria(self, role: str, crit_id: str) -> bool:
        self.ensure_role(role)
        orig = len(self._data[role])
        self._data[role] = [c for c in self._data[role] if c.get("id") != crit_id]
        changed = len(self._data[role]) != orig
        if changed:
            self._write(self._data)
        return changed

    def export_csv(self, out_path: str):
        import csv
        rows = []
        for role, crits in self._data.items():
            for c in crits:
                rows.append([role, c.get("id"), c.get("text"), "M" if c.get("mandatory") else "O"])
        with open(out_path, "w", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["role","id","text","mandatory"])
            writer.writerows(rows)
        return out_path

    def seed_defaults(self, overwrite: bool = False):
        if overwrite:
            data = _default_data()
        else:
            data = self._data
            for r in DEFAULT_ROLES:
                data.setdefault(r, [])
        # add concise example criteria if not present
        if not any(d.get("text","") for d in data["donor"]):
            self._data["donor"].append(asdict(Criteria(id=str(uuid.uuid4()), text="Post donation with name, weight, perishability, pickup window, and location.", mandatory=True)))
        if not any(d.get("text","") for d in data["recipient"]):
            self._data["recipient"].append(asdict(Criteria(id=str(uuid.uuid4()), text="Accept or reject matches; capacity updates when accepted.", mandatory=True)))
        if not any(d.get("text","") for d in data["volunteer"]):
            self._data["volunteer"].append(asdict(Criteria(id=str(uuid.uuid4()), text="Receive ordered route; mark pickup in-progress and completed.", mandatory=True)))
        if not any(d.get("text","") for d in data["admin"]):
            self._data["admin"].append(asdict(Criteria(id=str(uuid.uuid4()), text="View totals and export transactions CSV.", mandatory=True)))
        self._write(self._data)
        return True

# CLI
def main():
    p = argparse.ArgumentParser(description="Manage roles & acceptance criteria")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub_list_roles = sub.add_parser("list-roles")
    sub_list_criteria = sub.add_parser("list-criteria")
    sub_list_criteria.add_argument("--role", required=True)

    sub_add = sub.add_parser("add-criteria")
    sub_add.add_argument("--role", required=True)
    sub_add.add_argument("--text", required=True)
    sub_add.add_argument("--mandatory", action="store_true", help="Mark criteria mandatory (default true)")

    sub_remove = sub.add_parser("remove-criteria")
    sub_remove.add_argument("--role", required=True)
    sub_remove.add_argument("--id", required=True)

    sub_export = sub.add_parser("export-csv")
    sub_export.add_argument("--out", required=True)

    sub_seed = sub.add_parser("seed")
    sub_seed.add_argument("--overwrite", action="store_true")

    args = p.parse_args()
    mgr = RolesManager()

    if args.cmd == "list-roles":
        for r in mgr.list_roles():
            print(r)
    elif args.cmd == "list-criteria":
        for c in mgr.list_criteria(args.role):
            print(f"{c.id} | {'M' if c.mandatory else 'O'} | {c.text}")
    elif args.cmd == "add-criteria":
        c = mgr.add_criteria(args.role, args.text, mandatory=args.mandatory or True)
        print(f"Added: {c.id}")
    elif args.cmd == "remove-criteria":
        ok = mgr.remove_criteria(args.role, args.id)
        print("Removed" if ok else "Not found")
    elif args.cmd == "export-csv":
        path = mgr.export_csv(args.out)
        print("Exported to", path)
    elif args.cmd == "seed":
        mgr.seed_defaults(overwrite=args.overwrite)
        print("Seeded defaults to", mgr.path)

if __name__ == "__main__":
    main()
