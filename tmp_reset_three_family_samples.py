import os
import sqlite3


os.environ["APPLYPILOT_DIR"] = r"C:\Users\swazy\kobby18-batch-run"

from applypilot.config import DB_PATH  # noqa: E402


def main() -> None:
    ids = (1614, 1668, 1804)
    ids_sql = ",".join(str(x) for x in ids)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(f"update jobs set apply_status=null where apply_status='selected' and rowid not in ({ids_sql})")
    conn.execute(
        f"update jobs set apply_status='selected', tailored_resume_path=null, tailored_at=null, tailor_status=null, tailor_failure_detail=null, tailor_report_path=null, tailor_requirement_gaps=null, tailor_responsibility_map=null, tailor_attempts=0 where rowid in ({ids_sql})"
    )
    conn.commit()
    for row in conn.execute(
        f"select rowid, title, apply_status, tailor_status from jobs where rowid in ({ids_sql}) order by rowid"
    ):
        print(row)


if __name__ == "__main__":
    main()
