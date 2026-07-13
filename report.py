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
    total, scored, ha, communes = [x or 0 for x in cur.fetchone()]
    print(f"\n{scored} scored building zones · {ha} ha developable · "
          f"{communes} communes\n")

    # --- Top communes -------------------------------------------------------
    print("-" * 78)
    print("TOP 15 COMMUNES BY OPPORTUNITY")
    print("-" * 78)
    print(f"{'#':<3} {'Commune':<22} {'Score':>5} {'Zones':>6} {'Developable':>13}")
    print("-" * 78)
    cur.execute("""
        SELECT COALESCE(commune_name, '(bfs ' || commune_bfs || ')'),
               round(COALESCE(commune_score,0)::numeric, 0),
               COALESCE(n_building_zones, 0),
               round((COALESCE(total_developable_m2,0)/10000.0)::numeric, 1)
        FROM core.commune_opportunity
        WHERE COALESCE(commune_score,0) > 0
        ORDER BY commune_score DESC
        LIMIT 15
    """)
    for i, (name, score, nz, ha) in enumerate(cur.fetchall(), 1):
        print(f"{i:<3} {str(name)[:22]:<22} {score:>5} {nz:>6} {ha:>8} ha")

    # --- Top individual zones ----------------------------------------------
    print()
    print("-" * 78)
    print("TOP 20 LEADS — underbuilt D4A/D4B zones (Phase B)")
    print("-" * 78)
    cur.execute("""
        SELECT commune_name, zone_code, height_limit_m,
               round(developable_m2), round(opportunity_score::numeric, 0),
               round(utilisation_pct::numeric), n_buildings, n_raisable,
               score_reasons
        FROM core.zone_opportunity
        WHERE opportunity_score > 0 AND zone_tier = 'target'
        ORDER BY opportunity_score DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (no TARGET (D4A/D4B) zones found)")
    for i, (commune, code, h, dev, score, util, nb, rais, reasons) in enumerate(rows, 1):
        util_s = f"{int(util)}% built" if util is not None else "?"
        print(f"\n{i:>2}. {str(commune or '?'):<18} score {score}   [{code}]  {util_s}")
        print(f"    {int(dev or 0):,} m² ({(dev or 0)/10000:.1f} ha)"
              + (f" · {h}m limit" if h else "")
              + f" · {nb or 0} buildings"
              + (f" · {rais} RAISABLE" if rais else ""))
        if reasons:
            print(f"    {reasons}")

    # --- Underuse summary ---------------------------------------------------
    print()
    print("-" * 78)
    print("UNDERUSE SUMMARY — where the real headroom is")
    print("-" * 78)
    cur.execute("""
        SELECT
          count(*) FILTER (WHERE zone_tier='target' AND n_buildings = 0),
          count(*) FILTER (WHERE zone_tier='target' AND utilisation_pct < 40
                                 AND n_buildings > 0),
          count(*) FILTER (WHERE zone_tier='target' AND utilisation_pct >= 80),
          COALESCE(sum(n_raisable), 0)
        FROM core.zone_opportunity
    """)
    empty, under, full, rais = [x or 0 for x in cur.fetchone()]
    print(f"  {empty:>4} target zones are EMPTY (no buildings) — prime sites")
    print(f"  {under:>4} target zones are UNDERBUILT (<40% of permitted density)")
    print(f"  {full:>4} target zones are effectively FULL (>=80%) — skip these")
    print(f"  {rais:>4} buildings the canton says CAN BE RAISED (surelevation)")

    # --- Zone-type mix ------------------------------------------------------
    print()
    print("-" * 78)
    print("ZONE TYPE MIX (what kind of opportunities exist)")
    print("-" * 78)
    cur.execute("""
        SELECT zone_tier, zone_code, count(*),
               round((sum(developable_m2)/10000.0)::numeric)
        FROM core.zone_opportunity
        GROUP BY zone_tier, zone_code
        ORDER BY zone_tier, count(*) DESC
    """)
    for tier, code, n, ha in cur.fetchall():
        tag = {"target": ">> TARGET", "secondary": "   secondary",
               "avoid": "   avoid   "}.get(tier, tier)
        print(f"  {tag}  {str(code or '?'):<5} {n:>4} zones · {int(ha or 0):>5} ha")

    cur.close()
    conn.close()
    print("\n" + "=" * 78)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("\n[REPORT ERROR] " + str(e))
        traceback.print_exc()
