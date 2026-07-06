import sqlite3


DB_PATH = "/home/swazy364/code/ApplyPilot/docker-data/multi/workspaces/kobby18/applypilot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    print("BY_SITE")
    for row in conn.execute(
        "select coalesce(site,''), count(*) from jobs group by coalesce(site,'') order by count(*) desc"
    ):
        print(row)
    print("BY_STRATEGY")
    for row in conn.execute(
        "select coalesce(strategy,''), count(*) from jobs group by coalesce(strategy,'') order by count(*) desc"
    ):
        print(row)


if __name__ == "__main__":
    main()
