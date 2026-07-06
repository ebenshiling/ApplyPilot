import sqlite3
from pathlib import Path


DB_PATH = Path("/home/swazy364/code/ApplyPilot/docker-data/multi/workspaces/kobby18/applypilot.db")


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    for row in conn.execute(
        "select rowid, title, fit_score, coalesce(apply_status,''), coalesce(tailor_status,'') from jobs where apply_status='selected' order by fit_score desc, discovered_at desc limit 20"
    ):
        print(row)


if __name__ == "__main__":
    main()
