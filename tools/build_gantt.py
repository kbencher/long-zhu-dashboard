"""Build a Gantt chart from the Long Zhu Budget Google Sheet.

Source columns:
  A  active flag (TRUE/FALSE)
  B  Department
  C  Workstream group
  D  Owner / Name
  E  Notes
  G  Monthly cost
  H  Start date
  I  End date  (or '# of Months' for one-time projects)

Renders a horizontal Gantt:
  * one bar per task
  * color = owner
  * grouped by workstream
  * x-axis = months
  * no $ amounts shown
"""
import os, sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.dates import MonthLocator, DateFormatter
import gspread
from google.oauth2.service_account import Credentials

_KEY = os.environ.get('LONGZHU_KEY_PATH') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'longzhu-agent-key.json')


def connect_gsheets():
    creds = Credentials.from_service_account_file(_KEY, scopes=[
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ])
    return gspread.authorize(creds)


SHEET_KEY = '1rKFY6S-VZFnOkZLs_JeNtZSkFZIkyVbSROSrmx0rb40'
OUT_PNG   = os.path.join(os.path.dirname(__file__), 'long_zhu_gantt.png')


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def load_tasks():
    gc = connect_gsheets()
    sh = gc.open_by_key(SHEET_KEY)
    ws = sh.sheet1
    rows = ws.get('A1:I40', value_render_option='FORMATTED_VALUE')
    tasks = []
    section = 'Monthly'   # rows 7-12 are Monthly recurring
    for i, r in enumerate(rows, 1):
        if not r or len(r) < 8:
            continue
        # Section header detection: "One-Time (ish)" header row
        if len(r) > 1 and 'One-Time' in str(r[1]):
            section = 'One-Time'
            continue
        # Active flag in col A (rows 15+); rows 7-12 have no flag, treat as TRUE
        active_raw = str(r[0]).strip().upper()
        if section == 'Monthly':
            active = True
        else:
            active = (active_raw == 'TRUE')
        if not active:
            continue
        dept    = str(r[1] or '').strip()
        wstream = str(r[2] or '').strip() or section
        owner   = str(r[3] or '').strip()
        notes   = str(r[4] or '').strip() if len(r) > 4 else ''
        start   = _parse_date(r[7] if len(r) > 7 else '')
        if not start:
            continue
        # Col I is either end date (Monthly) or # of months (One-Time)
        col_i = str(r[8] or '').strip() if len(r) > 8 else ''
        end = _parse_date(col_i)
        if end is None:
            # Try to interpret as months
            try:
                n_months = int(float(col_i.replace(',', '')))
                end = start + relativedelta(months=n_months)
            except (ValueError, AttributeError):
                continue
        tasks.append({
            'dept': dept, 'workstream': wstream, 'owner': owner,
            'notes': notes, 'start': start, 'end': end, 'section': section,
        })
    return tasks


# Palette — distinct colors for each owner.  Order matters; assigned in
# first-seen order so monthly roles get the stronger colors at the top.
PALETTE = [
    '#5786d6',  # blue
    '#44997f',  # teal
    '#e74c3c',  # red
    '#f5a623',  # orange
    '#9b59b6',  # purple
    '#2ecc71',  # green
    '#e67e22',  # dark orange
    '#34495e',  # slate
    '#1abc9c',  # cyan
    '#c0392b',  # brick
    '#8e44ad',  # dark purple
    '#16a085',  # dark teal
    '#d35400',  # burnt orange
    '#7f8c8d',  # grey
]


def render_gantt(tasks, out_path=OUT_PNG):
    # Group tasks by workstream, preserve insertion order
    by_ws = {}
    for t in tasks:
        by_ws.setdefault(t['workstream'], []).append(t)

    # Assign colors to owners (consistent across chart)
    owners = []
    for t in tasks:
        if t['owner'] not in owners:
            owners.append(t['owner'])
    color_by_owner = {o: PALETTE[i % len(PALETTE)] for i, o in enumerate(owners)}

    # Build flat ordering with section headers
    ordered = []   # list of ('header', label) or ('task', task_dict)
    for ws_name, group_tasks in by_ws.items():
        ordered.append(('header', ws_name))
        for t in group_tasks:
            ordered.append(('task', t))

    fig_h = max(6, 0.42 * len(ordered) + 2.5)
    fig, ax = plt.subplots(figsize=(15, fig_h))

    y = 0
    yticks, ylabels = [], []
    for kind, item in reversed(ordered):     # reverse so first row is at top
        if kind == 'header':
            yticks.append(y)
            ylabels.append('')               # blank label, we'll annotate below
            ax.annotate(item, xy=(0, y), xycoords=('axes fraction', 'data'),
                        xytext=(8, 0), textcoords='offset points',
                        ha='left', va='center',
                        fontsize=11, fontweight='bold', color='#222')
        else:
            t = item
            duration_days = (t['end'] - t['start']).days
            label = t['notes'] if t['notes'] else f"{t['dept']} — {t['owner']}"
            if len(label) > 50:
                label = label[:47] + '…'
            ax.barh(y, duration_days, left=t['start'], height=0.55,
                    color=color_by_owner.get(t['owner'], '#999'),
                    edgecolor='white', linewidth=1.2)
            ax.annotate(t['owner'], xy=(t['end'], y),
                        xytext=(6, 0), textcoords='offset points',
                        ha='left', va='center', fontsize=8, color='#444')
            yticks.append(y)
            ylabels.append(label)
        y += 1

    ax.set_yticks(yticks)
    ax.set_yticklabels(ylabels, fontsize=9)

    # X-axis: monthly ticks, span the data range
    all_starts = [t['start'] for t in tasks]
    all_ends   = [t['end']   for t in tasks]
    ax.set_xlim(min(all_starts) - relativedelta(days=14),
                max(all_ends)   + relativedelta(days=14))
    ax.xaxis.set_major_locator(MonthLocator(bymonth=range(1, 13, 1)))
    ax.xaxis.set_major_formatter(DateFormatter('%b %y'))
    plt.setp(ax.get_xticklabels(), rotation=45, ha='right', fontsize=9)

    # Light vertical grid by month
    ax.grid(axis='x', linestyle=':', linewidth=0.7, color='#ccc', alpha=0.7)
    ax.set_axisbelow(True)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    ax.spines['left'].set_color('#888')
    ax.spines['bottom'].set_color('#888')

    ax.set_title('Long Zhu — Workstream Timeline',
                 fontsize=16, fontweight='bold', pad=14, loc='left')

    # Owner legend below the chart
    handles = [mpatches.Patch(color=c, label=o)
               for o, c in color_by_owner.items()]
    ax.legend(handles=handles, loc='upper center',
              bbox_to_anchor=(0.5, -0.08),
              ncol=min(7, len(handles)), frameon=False, fontsize=9,
              handlelength=1.2, handleheight=1.0, columnspacing=1.5)

    plt.tight_layout()
    plt.savefig(out_path, dpi=180, bbox_inches='tight', facecolor='white')
    print(f'Wrote {out_path}')
    return out_path


def main():
    tasks = load_tasks()
    print(f'Loaded {len(tasks)} active tasks')
    for t in tasks:
        dur_months = (t['end'] - t['start']).days // 30
        print(f"  {t['workstream']:<20} {t['owner']:<15} "
              f"{t['start'].strftime('%b %y')} → {t['end'].strftime('%b %y')} "
              f"({dur_months} mo) — {t['notes'][:40]}")
    render_gantt(tasks)


if __name__ == '__main__':
    main()
