import boto3
import csv
import io
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from datetime import datetime
from collections import Counter
from botocore.exceptions import (
    NoCredentialsError, PartialCredentialsError,
    EndpointResolutionError, ClientError, BotoCoreError
)
import json


# ── Constants ─────────────────────────────────────────────────────────────────

SYMPTOM_FIELDS = {
    'chest_pain':         'Chest Pain',
    'chills_or_heat':     'Chills/Heat',
    'depersonalization':  'Depersonalization',
    'derealization':      'Derealization',
    'dizziness':          'Dizziness',
    'fear_losing_control':'Fear of Losing Control',
    'fear_of_dying':      'Fear of Dying',
    'nausea':             'Nausea',
    'palpitations':       'Palpitations',
    'paresthesia':        'Numbness/Tingling',
    'shortness_of_breath':'Shortness of Breath',
    'sweating':           'Sweating',
    'trembling':          'Trembling',
}


# ── AWS Error Helper ──────────────────────────────────────────────────────────

def handle_aws_error(e, context="AWS"):
    """
    Returns a human-readable error message string for common AWS/boto3 errors.
    Call this whenever a boto3 operation might fail.
    """
    if isinstance(e, NoCredentialsError):
        return (
            f"[{context}] AWS credentials not found. "
            "Make sure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set "
            "(environment variables, ~/.aws/credentials, or IAM role)."
        )
    if isinstance(e, PartialCredentialsError):
        return (
            f"[{context}] Incomplete AWS credentials. "
            "Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be provided."
        )
    if isinstance(e, ClientError):
        code = e.response['Error']['Code']
        msg  = e.response['Error']['Message']
        if code in ('AccessDeniedException', 'AccessDenied'):
            return (
                f"[{context}] Access denied by AWS (IAM policy). "
                f"Check that your user/role has DynamoDB permissions. Details: {msg}"
            )
        if code == 'ResourceNotFoundException':
            return (
                f"[{context}] DynamoDB table 'PanicAttacks' not found in eu-west-1. "
                "Verify the table name and region."
            )
        if code == 'ExpiredTokenException':
            return (
                f"[{context}] AWS session token has expired. "
                "Refresh your credentials or renew the IAM role session."
            )
        return f"[{context}] AWS ClientError ({code}): {msg}"
    if isinstance(e, EndpointResolutionError):
        return (
            f"[{context}] Could not resolve AWS endpoint. "
            "Check your internet connection and that region 'eu-west-1' is correct."
        )
    if isinstance(e, BotoCoreError):
        return f"[{context}] AWS connection error: {str(e)}"
    return f"[{context}] Unexpected error: {str(e)}"


# ── Helper Functions ──────────────────────────────────────────────────────────

def get_table():
    """Returns the DynamoDB 'PanicAttacks' table resource."""
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    return dynamodb.Table("PanicAttacks")


def get_all_episodes():
    """
    Reads all episodes via Scan with full pagination.
    Scan is intentionally used here because the userId is encrypted by Alexa
    and differs per user — a Query on a fixed partition key would not apply.
    """
    try:
        table = get_table()
        response = table.scan()
        items = response.get("Items", [])

        # Pagination: load additional pages if table size exceeds 1 MB
        while "LastEvaluatedKey" in response:
            response = table.scan(
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            items.extend(response.get("Items", []))

        return items

    except (NoCredentialsError, PartialCredentialsError,
            EndpointResolutionError, ClientError, BotoCoreError) as e:
        raise RuntimeError(handle_aws_error(e, context="get_all_episodes"))


def decode_symptoms(item):
    """Returns a comma-separated list of human-readable names for active symptoms."""
    active = []
    for field, label in SYMPTOM_FIELDS.items():
        try:
            if int(item.get(field, 0)) == 1:
                active.append(label)
        except (ValueError, TypeError):
            pass
    return ', '.join(active) if active else 'None reported'


def format_date(date_string):
    """Converts YYYY-MM-DD → DD.MM.YYYY."""
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').strftime('%d.%m.%Y')
    except (ValueError, TypeError):
        return date_string


def compute_stats_and_charts(filtered_items):
    """Computes all statistics and chart data from the filtered item list."""

    # Symptom frequency counts
    symptom_counts = Counter()
    for item in filtered_items:
        for field, label in SYMPTOM_FIELDS.items():
            try:
                if int(item.get(field, 0)) == 1:
                    symptom_counts[label] += 1
            except (ValueError, TypeError):
                pass

    top_symptoms = symptom_counts.most_common(10)

    # Hourly distribution
    hour_dist = Counter()
    for item in filtered_items:
        try:
            hour_dist[int(item['attack_hour'])] += 1
        except (KeyError, ValueError, TypeError):
            pass

    # Weekday distribution
    weekday_dist = Counter()
    for item in filtered_items:
        try:
            weekday_dist[datetime.strptime(item['date'], '%Y-%m-%d').weekday()] += 1
        except (KeyError, ValueError):
            pass

    # Monthly distribution
    month_dist = Counter()
    for item in filtered_items:
        month = item.get('date', '')[:7]
        if month:
            month_dist[month] += 1

    # Trigger distribution
    trigger_data = Counter()
    for item in filtered_items:
        t = item.get('trigger', 'Unknown')
        if t:
            trigger_data[t] += 1

    # Average severity per trigger
    trigger_sev = {}
    for item in filtered_items:
        t = item.get('trigger', 'Unknown')
        try:
            trigger_sev.setdefault(t, []).append(int(item.get('severity', 0)))
        except (ValueError, TypeError):
            pass

    # Severity over time
    sev_by_date = {}
    for item in filtered_items:
        d = item.get('date', '')
        try:
            sev_by_date.setdefault(d, []).append(int(item.get('severity', 0)))
        except (ValueError, TypeError):
            pass

    # Duration vs. severity (scatter plot data)
    dur_sev = []
    for item in filtered_items:
        try:
            dur_sev.append({'x': int(item['duration_min']), 'y': int(item['severity'])})
        except (KeyError, ValueError, TypeError):
            pass

    sorted_dates = sorted(sev_by_date)
    trig_labels  = list(trigger_sev)

    stats = {
        'total_attacks': len(filtered_items),

        'avg_severity': round(
            sum(int(i.get('severity', 0)) for i in filtered_items) / len(filtered_items), 1
        ) if filtered_items else 0,

        'avg_duration': round(
            sum(int(i.get('duration_min', 0)) for i in filtered_items) / len(filtered_items), 1
        ) if filtered_items else 0,

        'most_common_trigger': trigger_data.most_common(1)[0][0] if trigger_data else 'N/A',
        'most_common_symptom': symptom_counts.most_common(1)[0][0] if symptom_counts else 'N/A',
    }

    charts = {
        'symptom_chart_labels':    json.dumps([s[0] for s in top_symptoms]),
        'symptom_chart_values':    json.dumps([s[1] for s in top_symptoms]),
        'hour_chart_labels':       json.dumps([f"{h}:00" for h in range(24)]),
        'hour_chart_values':       json.dumps([hour_dist.get(h, 0) for h in range(24)]),
        'weekday_chart_labels':    json.dumps(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']),
        'weekday_chart_values':    json.dumps([weekday_dist.get(i, 0) for i in range(7)]),
        'month_chart_labels':      json.dumps(sorted(month_dist)),
        'month_chart_values':      json.dumps([month_dist[m] for m in sorted(month_dist)]),
        'trigger_chart_labels':    json.dumps(list(trigger_data)),
        'trigger_chart_values':    json.dumps(list(trigger_data.values())),
        'severity_chart_labels':   json.dumps(sorted_dates),
        'severity_chart_values':   json.dumps([sum(sev_by_date[d]) / len(sev_by_date[d]) for d in sorted_dates]),
        'trigger_severity_labels': json.dumps(trig_labels),
        'trigger_severity_values': json.dumps([round(sum(trigger_sev[t]) / len(trigger_sev[t]), 1) for t in trig_labels]),
        'duration_severity_data':  json.dumps(dur_sev),
    }

    return stats, charts


# ── Views ─────────────────────────────────────────────────────────────────────

def dashboard_view(request):
    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')

    # Load all episodes (scan with pagination)
    try:
        items = get_all_episodes()
    except RuntimeError as e:
        # Surface AWS error directly in the Django messages framework
        messages.error(request, str(e))
        return render(request, "dashboard.html", {
            'data': [], 'stats': {}, 'date_from': date_from, 'date_to': date_to,
            'aws_error': str(e),
        })

    # Apply date filters
    if date_from:
        items = [i for i in items if i.get('date', '') >= date_from]
    if date_to:
        items = [i for i in items if i.get('date', '') <= date_to]

    # Sort chronologically descending
    items = sorted(items, key=lambda x: x.get('date', ''), reverse=True)

    # Attach human-readable symptoms and formatted date to each item
    for item in items:
        item['decoded_symptoms'] = decode_symptoms(item)
        item['formatted_date']   = format_date(item.get('date', ''))

    stats, charts = compute_stats_and_charts(items)

    context = {
        'data':      items,
        'stats':     stats,
        'date_from': date_from,
        'date_to':   date_to,
        **charts,
    }
    return render(request, "dashboard.html", context)


# ── Export: CSV ───────────────────────────────────────────────────────────────

def export_csv(request):
    """Downloads all episodes as a CSV file."""
    try:
        items = get_all_episodes()
    except RuntimeError as e:
        return HttpResponse(f"AWS Error: {e}", status=503)

    items = sorted(items, key=lambda x: x.get('date', ''))

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="panictrace_report.csv"'

    fieldnames = [
        'date', 'attack_hour', 'severity', 'duration_min', 'trigger',
        'symptoms_raw_text',
    ] + list(SYMPTOM_FIELDS.keys())

    writer = csv.DictWriter(response, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for item in items:
        row = {f: item.get(f, '') for f in fieldnames}
        if row.get('symptoms_raw_text') == 'auto-generated':
            row['symptoms_raw_text'] = ''
        writer.writerow(row)

    return response


# ── Export: PDF ───────────────────────────────────────────────────────────────

def export_pdf(request):
    """PDF report with extended statistics, symptom ranking,
    trigger analysis, weekday and time-of-day distribution."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.enums import TA_CENTER
    except ImportError:
        return HttpResponse(
            "ReportLab is not installed. Please run 'pip install reportlab'.",
            status=500,
        )

    try:
        items = get_all_episodes()
    except RuntimeError as e:
        return HttpResponse(f"AWS Error: {e}", status=503)

    items = sorted(items, key=lambda x: x.get('date', ''))

    # ── Colors ──
    BLUE      = colors.HexColor('#4A90D9')
    DARK_BLUE = colors.HexColor('#2C5F8A')
    LIGHT     = colors.HexColor('#F2F7FC')
    STRIPE    = colors.HexColor('#E8F0FA')
    RED       = colors.HexColor('#E74C3C')
    GREEN     = colors.HexColor('#27AE60')
    ORANGE    = colors.HexColor('#F39C12')

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.8*cm, bottomMargin=1.8*cm)

    styles   = getSampleStyleSheet()
    centered = ParagraphStyle('centered', parent=styles['Normal'], alignment=TA_CENTER)
    heading  = ParagraphStyle('myheading', parent=styles['Heading2'],
                              textColor=DARK_BLUE, spaceAfter=4)
    small    = ParagraphStyle('small', parent=styles['Normal'], fontSize=7)
    elements = []

    # ══ HEADER ══════════════════════════════════════════════════════════════
    elements.append(Paragraph("PanicTrace – Episode Report", styles['Title']))
    elements.append(Paragraph(
        f"Generated: {datetime.now().strftime('%d.%m.%Y %H:%M')}  |  "
        f"Total episodes: <b>{len(items)}</b>",
        centered))
    elements.append(HRFlowable(width="100%", thickness=1.5,
                               color=BLUE, spaceAfter=10))

    if not items:
        elements.append(Paragraph("No episodes recorded yet.", styles['Normal']))
        doc.build(elements)
        buffer.seek(0)
        resp = HttpResponse(buffer, content_type='application/pdf')
        resp['Content-Disposition'] = 'attachment; filename="panictrace_report.pdf"'
        return resp

    # ── Base data ──
    severities = []
    durations  = []
    for i in items:
        try: severities.append(int(i.get('severity', 0)))
        except: pass
        try: durations.append(int(i.get('duration_min', 0)))
        except: pass

    avg_sev = round(sum(severities) / len(severities), 1) if severities else 0
    avg_dur = round(sum(durations)  / len(durations),  1) if durations  else 0
    max_sev = max(severities) if severities else 0
    min_sev = min(severities) if severities else 0
    max_dur = max(durations)  if durations  else 0

    # ══ 1. OVERVIEW TILES (2×3) ═════════════════════════════════════════════
    elements.append(Paragraph("Overview Statistics", heading))

    def stat_cell(label, value, color=BLUE):
        return Paragraph(f'<font color="{color.hexval()}" size="16"><b>{value}</b></font>'
                         f'<br/><font size="8">{label}</font>', centered)

    overview = [
        [stat_cell("Total Episodes",    str(len(items)),  DARK_BLUE),
         stat_cell("Avg. Severity",     str(avg_sev),     ORANGE),
         stat_cell("Avg. Duration",     f"{avg_dur} min", GREEN)],
        [stat_cell("Max. Severity",     str(max_sev),     RED),
         stat_cell("Min. Severity",     str(min_sev),     GREEN),
         stat_cell("Max. Duration",     f"{max_dur} min", ORANGE)],
    ]
    ov_table = Table(overview, colWidths=[5.7*cm, 5.7*cm, 5.7*cm])
    ov_table.setStyle(TableStyle([
        ('BOX',        (0,0), (-1,-1), 0.5, colors.grey),
        ('INNERGRID',  (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('BACKGROUND', (0,0), (-1,-1), LIGHT),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(ov_table)
    elements.append(Spacer(1, 0.5*cm))

    # ══ 2. SYMPTOM RANKING ══════════════════════════════════════════════════
    elements.append(Paragraph("Symptom Frequency Ranking", heading))

    symptom_counts = Counter()
    for item in items:
        for field, label in SYMPTOM_FIELDS.items():
            try:
                if int(item.get(field, 0)) == 1:
                    symptom_counts[label] += 1
            except: pass

    total = len(items)
    sym_data = [['Rank', 'Symptom', 'Count', '% of Episodes', 'Bar']]
    for rank, (sym, cnt) in enumerate(symptom_counts.most_common(13), 1):
        pct  = round(cnt / total * 100, 1)
        bar  = '█' * int(pct / 5)
        sym_data.append([str(rank), sym, str(cnt), f"{pct}%", bar])

    sym_table = Table(sym_data, colWidths=[1.2*cm, 5*cm, 1.5*cm, 2.5*cm, 7*cm])
    sym_table.setStyle(TableStyle([
        ('BACKGROUND',     (0,0), (-1,0),  BLUE),
        ('TEXTCOLOR',      (0,0), (-1,0),  colors.white),
        ('FONTNAME',       (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, STRIPE]),
        ('GRID',           (0,0), (-1,-1), 0.3, colors.lightgrey),
        ('ALIGN',          (2,0), (-1,-1), 'CENTER'),
        ('TEXTCOLOR',      (4,1), (4,-1),  BLUE),
    ]))
    elements.append(sym_table)
    elements.append(Spacer(1, 0.5*cm))

    # ══ 3. TRIGGER ANALYSIS ═════════════════════════════════════════════════
    elements.append(Paragraph("Trigger Analysis", heading))

    trigger_data = Counter()
    trigger_sev  = {}
    for item in items:
        t = item.get('trigger', 'Unknown') or 'Unknown'
        trigger_data[t] += 1
        try:
            trigger_sev.setdefault(t, []).append(int(item.get('severity', 0)))
        except: pass

    trig_rows = [['Trigger', 'Count', '% of Episodes', 'Avg. Severity']]
    for trig, cnt in trigger_data.most_common():
        pct   = round(cnt / total * 100, 1)
        avg_s = round(sum(trigger_sev[trig]) / len(trigger_sev[trig]), 1)
        trig_rows.append([trig, str(cnt), f"{pct}%", str(avg_s)])

    trig_table = Table(trig_rows, colWidths=[6*cm, 2*cm, 3.5*cm, 3.5*cm])
    trig_table.setStyle(TableStyle([
        ('BACKGROUND',     (0,0), (-1,0),  DARK_BLUE),
        ('TEXTCOLOR',      (0,0), (-1,0),  colors.white),
        ('FONTNAME',       (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, STRIPE]),
        ('GRID',           (0,0), (-1,-1), 0.3, colors.lightgrey),
        ('ALIGN',          (1,0), (-1,-1), 'CENTER'),
    ]))
    elements.append(trig_table)
    elements.append(Spacer(1, 0.5*cm))

    # ══ 4. TEMPORAL DISTRIBUTION ════════════════════════════════════════════
    elements.append(Paragraph("Temporal Distribution", heading))

    # Weekday
    weekday_dist  = Counter()
    weekday_names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
    for item in items:
        try:
            wd = datetime.strptime(item['date'], '%Y-%m-%d').weekday()
            weekday_dist[wd] += 1
        except: pass

    wd_max  = max(weekday_dist.values()) if weekday_dist else 1
    wd_rows = [['Day', 'Count', 'Distribution']]
    for i, name in enumerate(weekday_names):
        cnt = weekday_dist.get(i, 0)
        bar = '█' * int(cnt / wd_max * 20)
        wd_rows.append([name, str(cnt), bar])

    # Time-of-day blocks (Morning / Afternoon / Evening / Night)
    tod_dist = {'Night (0–5)': 0, 'Morning (6–11)': 0,
                'Afternoon (12–17)': 0, 'Evening (18–23)': 0}
    for item in items:
        try:
            h = int(item.get('attack_hour', 0))
            if   h < 6:  tod_dist['Night (0–5)']      += 1
            elif h < 12: tod_dist['Morning (6–11)']    += 1
            elif h < 18: tod_dist['Afternoon (12–17)'] += 1
            else:        tod_dist['Evening (18–23)']   += 1
        except: pass

    tod_max  = max(tod_dist.values()) if any(tod_dist.values()) else 1
    tod_rows = [['Time of Day', 'Count', 'Distribution']]
    for label, cnt in tod_dist.items():
        bar = '█' * int(cnt / tod_max * 20)
        tod_rows.append([label, str(cnt), bar])

    # Render side by side
    wd_table  = Table(wd_rows,  colWidths=[2*cm, 1.5*cm, 5*cm])
    tod_table = Table(tod_rows, colWidths=[3.5*cm, 1.5*cm, 4*cm])
    bar_style = TableStyle([
        ('BACKGROUND',     (0,0), (-1,0),  GREEN),
        ('TEXTCOLOR',      (0,0), (-1,0),  colors.white),
        ('FONTNAME',       (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, STRIPE]),
        ('GRID',           (0,0), (-1,-1), 0.3, colors.lightgrey),
        ('ALIGN',          (1,0), (1,-1),  'CENTER'),
        ('TEXTCOLOR',      (2,1), (2,-1),  GREEN),
    ])
    wd_table.setStyle(bar_style)
    tod_table.setStyle(bar_style)

    combined = Table([[wd_table, Spacer(0.5*cm, 1), tod_table]],
                     colWidths=[8.8*cm, 0.5*cm, 9*cm])
    elements.append(combined)
    elements.append(Spacer(1, 0.5*cm))

    # ══ 5. SEVERITY OVER TIME ═══════════════════════════════════════════════
    elements.append(Paragraph("Severity Over Time", heading))

    sev_by_date = {}
    for item in items:
        d = item.get('date', '')
        try:
            sev_by_date.setdefault(d, []).append(int(item.get('severity', 0)))
        except: pass

    sev_rows = [['Date', 'Episodes', 'Avg. Severity', 'Severity Bar']]
    for d in sorted(sev_by_date):
        vals = sev_by_date[d]
        avg  = round(sum(vals) / len(vals), 1)
        bar  = '█' * int(avg * 2)
        sev_rows.append([format_date(d), str(len(vals)), str(avg), bar])

    sev_table = Table(sev_rows, colWidths=[2.5*cm, 2*cm, 3*cm, 9.7*cm])
    sev_table.setStyle(TableStyle([
        ('BACKGROUND',     (0,0), (-1,0),  RED),
        ('TEXTCOLOR',      (0,0), (-1,0),  colors.white),
        ('FONTNAME',       (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, STRIPE]),
        ('GRID',           (0,0), (-1,-1), 0.3, colors.lightgrey),
        ('ALIGN',          (1,0), (2,-1),  'CENTER'),
        ('TEXTCOLOR',      (3,1), (3,-1),  RED),
    ]))
    elements.append(sev_table)
    elements.append(Spacer(1, 0.5*cm))

    # ══ 6. EPISODES TABLE ═══════════════════════════════════════════════════
    elements.append(HRFlowable(width="100%", thickness=1, color=BLUE, spaceAfter=6))
    elements.append(Paragraph("All Recorded Episodes", heading))

    ep_header = ['Date', 'Time', 'Sev.', 'Dur.\n(min)', 'Trigger', 'Active Symptoms']
    ep_data   = [ep_header]
    for item in items:
        sev = int(item.get('severity', 0))
        ep_data.append([
            format_date(item.get('date', '')),
            str(item.get('attack_hour', '')),
            str(sev),
            str(item.get('duration_min', '')),
            Paragraph(str(item.get('trigger', '')), small),
            Paragraph(decode_symptoms(item), small),
        ])

    ep_table = Table(ep_data, colWidths=[2.3*cm, 1.2*cm, 1.1*cm, 1.3*cm, 3.8*cm, 7.6*cm])
    ep_style  = TableStyle([
        ('BACKGROUND',     (0,0), (-1,0),  BLUE),
        ('TEXTCOLOR',      (0,0), (-1,0),  colors.white),
        ('FONTNAME',       (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 7.5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, STRIPE]),
        ('GRID',           (0,0), (-1,-1), 0.3, colors.lightgrey),
        ('VALIGN',         (0,0), (-1,-1), 'TOP'),
        ('ALIGN',          (2,0), (3,-1),  'CENTER'),
    ])
    # Highlight rows with severity ≥ 8 in red
    for row_idx, item in enumerate(items, start=1):
        try:
            if int(item.get('severity', 0)) >= 8:
                ep_style.add('BACKGROUND', (2, row_idx), (2, row_idx),
                             colors.HexColor('#FDECEA'))
        except: pass
    ep_table.setStyle(ep_style)
    elements.append(ep_table)

    # ══ FOOTER ══════════════════════════════════════════════════════════════
    elements.append(Spacer(1, 0.4*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elements.append(Paragraph(
        "PanicTrace · Bachelor Thesis · FH Hagenberg 2026",
        ParagraphStyle('footer', parent=styles['Normal'],
                       fontSize=7, textColor=colors.grey, alignment=TA_CENTER)))

    doc.build(elements)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="panictrace_report.pdf"'
    return response


# ── Manual Entry ──────────────────────────────────────────────────────────────

def add_attack_view(request):
    """View for manually adding a panic attack episode."""

    if request.method == 'POST':
        USER_ID = request.user.username if request.user.is_authenticated else "admin"
        if not USER_ID:
            messages.error(request, "User not authenticated.")
            return redirect("dashboard")
        try:
            table = get_table()
        except RuntimeError as e:
            messages.error(request, str(e))
            return render(request, "add_attack.html")

        attack_data = {
            'userId':            USER_ID,
            'timestamp':         datetime.now().isoformat(),
            'date':              request.POST.get('date'),
            'attack_hour':       request.POST.get('attack_hour'),
            'severity':          request.POST.get('severity'),
            'duration_min':      request.POST.get('duration_min'),
            'trigger':           request.POST.get('trigger'),
            'symptoms_raw_text': request.POST.get('symptoms_raw_text', ''),
            'source':            'django-web',
            'unmapped_terms':    '',
        }

        # Symptom checkboxes: 1 if checked, 0 otherwise
        for field in SYMPTOM_FIELDS:
            attack_data[field] = 1 if request.POST.get(field) else 0

        try:
            table.put_item(Item=attack_data)
            messages.success(request, 'Panic attack recorded successfully!')
            return redirect('dashboard')
        except (ClientError, BotoCoreError) as e:
            messages.error(request, handle_aws_error(e, context="add_attack_view"))

    print("SAVED SUCCESSFULLY")

    return render(request, "add_attack.html")