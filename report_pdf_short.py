"""
report_pdf_short.py — a tight 1-2 page PDF to SEND to a promoter.

Leads-first, minimal method. Run on Railway:  python report_pdf_short.py
Serves the file over HTTP (open the service's public URL to download), or set
SERVE=false to print base64.
"""
import os
import base64
from datetime import date

import psycopg2
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)

from config import DATABASE_URL

NAVY = colors.HexColor("#1F3A5F")
TEAL = colors.HexColor("#2E7D6F")
SLATE = colors.HexColor("#3E5C76")
LIGHT = colors.HexColor("#EEF2F6")
GREY = colors.HexColor("#5A5A5A")
AMBER = colors.HexColor("#B26A00")
RED = colors.HexColor("#9A2B2B")
WHITE = colors.white

OUT = os.getenv("OUTPUT_DIR", "/tmp")
PDF_PATH = os.path.join(OUT, "geneva_land_shortlist.pdf")

ss = getSampleStyleSheet()
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontName="Helvetica",
                      fontSize=9, leading=12.5, textColor=colors.HexColor("#222222"),
                      alignment=TA_LEFT, spaceAfter=4)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=7.5, textColor=GREY, spaceAfter=2)
LEAD = ParagraphStyle("LEAD", parent=BODY, fontSize=9.5, spaceAfter=1)
LEADSUB = ParagraphStyle("LEADSUB", parent=BODY, fontSize=8, textColor=SLATE, spaceAfter=0)


def fetch(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    n_target, n_empty, n_under, n_full, n_rais, ha = fetch(cur, """
        SELECT
          count(*) FILTER (WHERE zone_tier='target'),
          count(*) FILTER (WHERE zone_tier='target' AND n_buildings=0),
          count(*) FILTER (WHERE zone_tier='target' AND utilisation_pct<40 AND n_buildings>0),
          count(*) FILTER (WHERE zone_tier='target' AND utilisation_pct>=80),
          COALESCE(sum(n_raisable),0),
          COALESCE(round((sum(developable_m2) FILTER (WHERE zone_tier='target')/10000.0)::numeric,0),0)
        FROM core.zone_opportunity
    """)[0]

    leads = fetch(cur, """
        SELECT commune_name, zone_code, height_limit_m,
               round(developable_m2::numeric), round(opportunity_score::numeric),
               round(utilisation_pct::numeric), n_buildings, n_raisable
        FROM core.zone_opportunity
        WHERE zone_tier='target' AND opportunity_score>0
        ORDER BY opportunity_score DESC LIMIT 15
    """)
    cur.close(); conn.close()

    doc = SimpleDocTemplate(
        PDF_PATH, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=14*mm,
        title="Geneva — Development Land Shortlist")
    S = []

    # --- Header ---
    S.append(Paragraph('<font size="19" color="#1F3A5F"><b>Geneva — Development Land Shortlist</b></font>', BODY))
    S.append(Paragraph('<font size="10.5" color="#2E7D6F">Underbuilt mid-density zones (D4A / D4B) — apartment-block potential</font>', BODY))
    S.append(Spacer(1, 3*mm))
    S.append(Paragraph(
        f'Across the Canton of Geneva there are <b>{n_target} mid-density development zones</b>. '
        f'Of these, <b>{n_empty} are empty</b> and <b>{n_under} are significantly underbuilt</b> '
        f'(under 40% of permitted density) — the {n_empty+n_under} genuine opportunities below. '
        f'A further <b>{n_full}</b> are already at capacity and excluded.', BODY))
    S.append(Spacer(1, 3*mm))

    # --- KPI strip ---
    strip = Table([[
        Paragraph(f'<font size="16" color="#1F3A5F"><b>{n_empty}</b></font><br/>'
                  f'<font size="7.5" color="#5A5A5A">empty sites</font>', BODY),
        Paragraph(f'<font size="16" color="#1F3A5F"><b>{n_under}</b></font><br/>'
                  f'<font size="7.5" color="#5A5A5A">underbuilt</font>', BODY),
        Paragraph(f'<font size="16" color="#1F3A5F"><b>{int(ha)}</b></font><br/>'
                  f'<font size="7.5" color="#5A5A5A">ha in target zones</font>', BODY),
        Paragraph(f'<font size="16" color="#1F3A5F"><b>{n_rais}</b></font><br/>'
                  f'<font size="7.5" color="#5A5A5A">raisable buildings*</font>', BODY),
    ]], colWidths=[43*mm]*4)
    strip.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), LIGHT),
        ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("LINEBEFORE", (1,0), (-1,-1), 0.5, colors.HexColor("#D5DDE5")),
    ]))
    S.append(strip)
    S.append(Spacer(1, 5*mm))

    # --- Leads table ---
    S.append(Paragraph('<font size="12" color="#1F3A5F"><b>Top sites</b></font>', BODY))
    S.append(Spacer(1, 1.5*mm))

    header = ["#", "Commune", "Zone", "Size", "Height", "Built", "Status"]
    data = [header]
    for i, (com, code, h, dev, score, util, nb, rais) in enumerate(leads, 1):
        dev = int(dev or 0)
        if (nb or 0) == 0:
            status = "EMPTY"
        elif util is not None and util < 20:
            status = "barely built"
        elif util is not None:
            status = "underbuilt"
        else:
            status = "—"
        if rais:
            status += f" · {rais} raisable"
        util_txt = f"{int(util)}%" if util is not None else "0%"
        data.append([str(i), com, code, f"{dev/10000:.1f} ha",
                     f"{h or '?'} m", util_txt, status])

    t = Table(data, colWidths=[8*mm, 42*mm, 15*mm, 20*mm, 16*mm, 14*mm, 47*mm])
    style = [
        ("BACKGROUND", (0,0), (-1,0), NAVY),
        ("TEXTCOLOR", (0,0), (-1,0), WHITE),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LIGHT]),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D5DDE5")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (3,0), (5,-1), "CENTER"),
        ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]
    # colour the status cells
    for r, row in enumerate(data[1:], 1):
        st = row[6]
        if st.startswith("EMPTY"):
            style.append(("TEXTCOLOR", (6,r), (6,r), TEAL))
            style.append(("FONTNAME", (6,r), (6,r), "Helvetica-Bold"))
        elif "raisable" in st:
            style.append(("TEXTCOLOR", (6,r), (6,r), RED))
        elif st.startswith("barely"):
            style.append(("TEXTCOLOR", (6,r), (6,r), AMBER))
    t.setStyle(TableStyle(style))
    S.append(t)
    S.append(Spacer(1, 4*mm))

    # --- Footer / method ---
    S.append(Paragraph(
        '<b>Built</b> = share of the zone\'s legally permitted density already constructed; '
        'lower means more room to develop. Figures derived from official Canton of Geneva open '
        'data (SITG): zoning, building footprints and floor counts.', SMALL))
    S.append(Paragraph(
        '*Buildings the canton has formally identified as raisable under LCI art. 23 &amp; 27 '
        '(surélévation) — a separate "add floors" opportunity.', SMALL))
    S.append(Spacer(1, 2*mm))
    S.append(Paragraph(
        f'Screening analysis, {date.today().strftime("%B %Y")}. Indicates where to look; not a '
        'substitute for site-level due diligence (ownership, availability, ground and heritage '
        'constraints). Not legal or investment advice.', SMALL))

    doc.build(S)
    print(f"PDF written: {PDF_PATH} ({os.path.getsize(PDF_PATH):,} bytes) · {len(leads)} leads")

    if os.getenv("SERVE", "true").lower() == "true":
        import http.server, socketserver
        port = int(os.getenv("PORT", "8080"))

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                with open(PDF_PATH, "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 'attachment; filename="geneva_land_shortlist.pdf"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        print(f"Serving on port {port}. Open the Railway public URL to download.")
        with socketserver.TCPServer(("0.0.0.0", port), H) as httpd:
            httpd.serve_forever()
    else:
        with open(PDF_PATH, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        print("\n----- BEGIN PDF BASE64 -----")
        for i in range(0, len(b64), 200):
            print(b64[i:i+200])
        print("----- END PDF BASE64 -----")


if __name__ == "__main__":
    main()
