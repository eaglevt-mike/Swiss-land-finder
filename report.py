"""
report.py — read the Phase A results and print the promoter-facing shortlist.

This is the OUTPUT of the whole pipeline: ranked communes and the specific
zones driving each ranking. Run with start command:  python report.py
"""
import os
import psycopg2
from config import DATABASE_URL


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("=" * 78)
    print("GENEVA — DEVELOPMENT OPPORTUNITY SHORTLIST (Phase A)")
    print("=" * 78)

    # Headline numbers
    cur.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE opportunity_score > 0),
               round(sum(developable_m2)/10000.0),
               count(DISTINCT commune_bfs)
        FROM core.zone_opportunity
    """)
    total, scored, ha, communes = cur.fetchone()
    print(f"\n{scored} scored building zones · {ha} ha developable · "
          f"{communes} communes\n")

    # --- Top communes -------------------------------------------------------
    print("-" * 78)
    print("TOP 15 COMMUNES BY OPPORTUNITY")
    print("-" * 78)
    print(f"{'#':<3} {'Commune':<22} {'Score':>5} {'Zones':>6} {'Developable':>13}")
    print("-" * 78)
    cur.execute("""
        SELECT co.commune_bfs,
               COALESCE(MAX(zo.primary_use), '(bfs ' || co.commune_bfs || ')'),
               round(co.commune_score::numeric, 0),
               co.n_building_zones,
               round((co.total_developable_m2/10000.0)::numeric, 1)
        FROM core.commune_opportunity co
        LEFT JOIN core.zone_opportunity zo ON zo.commune_bfs = co.commune_bfs
        GROUP BY co.commune_bfs, co.commune_score, co.n_building_zones,
                 co.total_developable_m2
        ORDER BY co.commune_score DESC
        LIMIT 15
    """)
    for i, (bfs, name, score, nz, ha) in enumerate(cur.fetchall(), 1):
        print(f"{i:<3} {str(name)[:22]:<22} {score:>5} {nz:>6} {ha:>10} ha")

    # --- Top individual zones ----------------------------------------------
    print()
    print("-" * 78)
    print("TOP 20 INDIVIDUAL ZONES (the actual leads)")
    print("-" * 78)
    cur.execute("""
        SELECT primary_use, zone_type, round(developable_m2),
               round(opportunity_score::numeric, 0), score_reasons
        FROM core.zone_opportunity
        WHERE opportunity_score > 0
        ORDER BY opportunity_score DESC
        LIMIT 20
    """)
    for i, (commune, ztype, dev, score, reasons) in enumerate(cur.fetchall(), 1):
        print(f"\n{i:>2}. {str(commune or '?'):<18} score {score}")
        print(f"    {str(ztype or '?')[:60]}")
        print(f"    {int(dev):,} m²  ({dev/10000:.1f} ha)")
        if reasons:
            print(f"    why: {reasons}")

    # --- Zone-type mix ------------------------------------------------------
    print()
    print("-" * 78)
    print("ZONE TYPE MIX (what kind of opportunities exist)")
    print("-" * 78)
    cur.execute("""
        SELECT zone_type, count(*), round(sum(developable_m2)/10000.0)
        FROM core.zone_opportunity
        WHERE opportunity_score > 0
        GROUP BY zone_type
        ORDER BY count(*) DESC
        LIMIT 12
    """)
    for ztype, n, ha in cur.fetchall():
        print(f"  {n:>4} zones · {int(ha or 0):>5} ha · {str(ztype)[:50]}")

    cur.close()
    conn.close()
    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
