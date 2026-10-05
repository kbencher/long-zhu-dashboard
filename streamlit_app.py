"""Long Zhu — Workstream Gantt dashboard.

Reads the budget Google Sheet, builds a filterable Gantt chart styled per
the client mockup (workstream-color bars, owner labels overlaid, today
marker, filter pills at top).
"""
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ── Page setup ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title='Long Zhu — Workstream Timeline',
    page_icon='📅',
    layout='wide',
)


# ── Workstream colours (match mockup) ───────────────────────────────────────
# Keyed by the sheet's "Chart" column (C).
WORKSTREAM_COLORS = {
    'Game Development': '#A03D2D',   # red
    'Go-To-Market':     '#1F5A8C',   # blue
    '_hidden':          'rgba(0,0,0,0)',   # filtered-out rows
}
# Rounds whose bars are cross-hatched (Seed | Launch, Seed | Pre-Order…);
# everything else (Pre-Seed) is solid.
HATCHED_ROUND_PREFIX = 'seed'

# Highlighted months: shaded column + label in the header row above
# Monthly Burn.  Multi-word labels stack one word per line.  full=True
# extends the shading up through the header (label, burn rows, month).
MILESTONES = [
    (datetime(2027, 3, 1), 'Demo Deck',           True),
    (datetime(2027, 5, 1), 'Production Deck',     True),
    (datetime(2027, 8, 1), 'Launch',              True),
]
# Tasks whose bar switches to cross-hatched (Seed-funded) partway through:
# (Notes prefix, first hatched month).  The bar is split into a solid and a
# hatched segment on the same row; each segment shows its own cost.
FUNDING_SPLITS = [
    ('Community Management Plan', datetime(2027, 6, 1)),
]

# Header rows above the chart (burn rows), bottom-up spacing in px.
HEADER_ROW_BOTTOM = 42
HEADER_ROW_STEP = 20
CUMULATIVE_GROUPS = ['Game Development', 'Go-To-Market']
_N_HEADER_ROWS = 2
MILESTONE_LABEL_YSHIFT = HEADER_ROW_BOTTOM + HEADER_ROW_STEP * _N_HEADER_ROWS - 6
HEADER_TOP_MARGIN = MILESTONE_LABEL_YSHIFT + 84
MILESTONE_LABEL_HEIGHT = 34     # px — two stacked lines + padding

# Color palette when "Color by: Round" is selected.  Extend as new rounds
# (Series A, Series B…) appear in the sheet.
ROUND_COLORS = {
    'Pre-Seed': '#C9A227',   # gold
    'Launch':   '#86878B',   # platinum (cool grey-silver)
    'Seed':     '#5B47B0',   # purple
    'Series A': '#2D6A3F',   # green
    'Series B': '#A03D2D',   # red
}
FILTER_TO_WS = {
    'All':              None,
    'Game Development': 'Game Development',
    'Go-To-Market':     'Go-To-Market',
}


# ── Data ────────────────────────────────────────────────────────────────────
SHEET_KEY = '1rKFY6S-VZFnOkZLs_JeNtZSkFZIkyVbSROSrmx0rb40'


TASKS_TAB_GID = 1740834373   # 'Data Chartv2' — Gantt-input layout


@st.cache_data(ttl=300)
def load_tasks() -> pd.DataFrame:
    """Pull tasks from the Gantt-input tab of the Long Zhu Budget sheet.

    Columns are located by the header row (the row containing 'Stream' and
    'Start Date'), so inserting/reordering columns in the sheet is safe:
        Stream      — sheet section (Game Development, Marketing, Community…)
        Chart       — chart grouping / legend (Game Development, Go-To-Market)
        Owner, Notes, Round, Start Date (mm/dd/yy), Months, Cost
    Section header rows and rows missing Start Date or Months are skipped.
    Rows are grouped by Chart (in order of first appearance), keeping sheet
    order within each group.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        dict(st.secrets['gcp_service_account']),
        scopes=['https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_KEY)
    # Find the worksheet by gid (more stable than tab name)
    ws = next((w for w in sh.worksheets() if w.id == TASKS_TAB_GID), None)
    if ws is None:
        raise RuntimeError(f'Tasks tab (gid {TASKS_TAB_GID}) not found.')
    rows = ws.get('A1:Z200', value_render_option='FORMATTED_VALUE')

    hdr_i = next((i for i, r in enumerate(rows)
                  if 'Stream' in [str(c).strip() for c in r]
                  and 'Start Date' in [str(c).strip() for c in r]), None)
    if hdr_i is None:
        raise RuntimeError("Header row with 'Stream' and 'Start Date' not found.")
    col = {str(c).strip(): j for j, c in enumerate(rows[hdr_i]) if str(c).strip()}
    for need in ('Stream', 'Owner', 'Notes', 'Round', 'Start Date', 'Months', 'Cost'):
        if need not in col:
            raise RuntimeError(f"Column '{need}' not found in header row.")

    def _parse_date(s):
        if not s:
            return None
        for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(str(s).strip(), fmt)
            except ValueError:
                pass
        return None

    def _money(s):
        if not s: return 0.0
        s = str(s).replace('$','').replace(',','').strip()
        try: return float(s)
        except (ValueError, TypeError): return 0.0

    def _cell(r, name):
        j = col.get(name)
        return str(r[j] or '').strip() if j is not None and j < len(r) else ''

    out = []
    for r in rows[hdr_i + 1:]:
        stream     = _cell(r, 'Stream')
        chart      = _cell(r, 'Chart')
        owner      = _cell(r, 'Owner')
        notes      = _cell(r, 'Notes')
        round_     = _cell(r, 'Round')
        start      = _parse_date(_cell(r, 'Start Date'))
        months_raw = _cell(r, 'Months')
        total_cost = _money(_cell(r, 'Cost'))

        if not start or not months_raw:
            continue
        try:
            n_months = int(float(months_raw.replace(',', '')))
        except (ValueError, AttributeError):
            continue
        end = start + relativedelta(months=n_months)
        monthly_cost = total_cost / n_months if n_months else 0.0

        out.append({
            'workstream': chart or stream,
            'hatched':    round_.lower().startswith(HATCHED_ROUND_PREFIX),
            # 'Seed | Launch' → color by the first round listed
            'round':      round_.split('|')[0].strip() or 'Unspecified',
            'sub':        stream,
            'department': stream,
            'owner':      owner or 'TBD',
            'notes':      notes,
            'start':      start,
            'end':        end,
            'months':     n_months,
            'total_cost': total_cost,
            'monthly_cost': monthly_cost,
        })
    df = pd.DataFrame(out)
    if df.empty:
        return df
    group_order = {g: i for i, g in enumerate(dict.fromkeys(df['workstream']))}
    return (df.assign(_g=df['workstream'].map(group_order))
              .sort_values('_g', kind='stable')
              .drop(columns='_g')
              .reset_index(drop=True))


def _wrap_label(text: str, width: int = 38) -> str:
    """Insert <br> tags at word boundaries so a long label wraps."""
    words = (text or '').split()
    if not words:
        return ''
    lines, current = [], ''
    for w in words:
        if current and len(current) + 1 + len(w) > width:
            lines.append(current)
            current = w
        else:
            current = f'{current} {w}' if current else w
    if current:
        lines.append(current)
    return '<br>'.join(lines)


def _task_label(row) -> str:
    """Bold, word-wrapped task name (owner now shown only on the bar)."""
    primary = row['notes'] if row['notes'] else row['department']
    return f"<b>{_wrap_label(primary)}</b>"


# ── Build Plotly Gantt ──────────────────────────────────────────────────────
def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Preserve the order tasks appear in the Google Sheet and build unique
    y-axis labels.  Run this once on the *full* dataset before any filtering
    so label identities stay stable across the unfiltered / filtered views."""
    df = df.copy().reset_index(drop=True)
    df['_label'] = df.apply(_task_label, axis=1)
    # Make labels unique (Plotly's category axis dedupes identical labels).
    df['_label'] = [f"{lbl}<span style='display:none'>{i}</span>"
                     for i, lbl in enumerate(df['_label'])]
    df['_color'] = df['workstream'].map(WORKSTREAM_COLORS)

    # Split bars per FUNDING_SPLITS (both segments keep the row's _label).
    rows = []
    for _, r in df.iterrows():
        split = next((d for prefix, d in FUNDING_SPLITS
                      if r['notes'].startswith(prefix)
                      and r['start'] < d < r['end']), None)
        if split is None:
            rows.append(r)
            continue
        for seg_start, seg_end, hatched in ((r['start'], split, r['hatched']),
                                            (split, r['end'], True)):
            seg = r.copy()
            seg['start'], seg['end'], seg['hatched'] = seg_start, seg_end, hatched
            seg['months'] = ((seg_end.year - seg_start.year) * 12
                             + seg_end.month - seg_start.month)
            seg['total_cost'] = r['monthly_cost'] * seg['months']
            rows.append(seg)
    return pd.DataFrame(rows).reset_index(drop=True)


ROW_HEIGHT_PX = 50      # fixed height per task row


def _cuboid(x0, x1, y0, y1, z0, z1, color, name, hover):
    """Return a Plotly Mesh3d cuboid (used in the 3D cost timeline)."""
    # 8 vertices of the box
    xs = [x0, x1, x1, x0, x0, x1, x1, x0]
    ys = [y0, y0, y1, y1, y0, y0, y1, y1]
    zs = [z0, z0, z0, z0, z1, z1, z1, z1]
    # 12 triangles (i, j, k) covering all 6 faces (2 tris each)
    i = [0, 0,  4, 4,  0, 0,  3, 3,  0, 0,  1, 1]
    j = [1, 2,  5, 6,  1, 5,  2, 6,  3, 7,  2, 6]
    k = [2, 3,  6, 7,  5, 4,  6, 7,  7, 4,  6, 5]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=1.0, flatshading=True,
        name=name, hovertext=hover, hoverinfo='text',
        showscale=False,
    )


def render_gantt_3d(df: pd.DataFrame, color_by: str = 'workstream'):
    """3D version of the Gantt: each task is a rectangular column extruded
    along the time axis (X), the task row (Y), with HEIGHT (Z) = monthly cost.
    """
    if df.empty:
        return None
    df = df.copy().reset_index(drop=True)
    color_col, color_map = (
        ('round', ROUND_COLORS) if color_by == 'round'
        else ('workstream', WORKSTREAM_COLORS)
    )

    # X = days since the earliest start (numeric)
    x_origin = df['start'].min().replace(day=1)
    x_end_max = (df['end'].max() + relativedelta(months=1)).replace(day=1)

    def _to_days(d):
        return (d - x_origin).days

    fig = go.Figure()
    for idx, row in df.iterrows():
        x0 = _to_days(row['start'])
        x1 = _to_days(row['end'])
        y0 = idx - 0.4
        y1 = idx + 0.4
        z0 = 0
        z1 = float(row['monthly_cost']) or 0.0
        color = color_map.get(row[color_col], '#999')
        hover = (f"<b>{row['notes'] or row['department']}</b><br>"
                 f"Owner: {row['owner']}<br>"
                 f"{row['start'].strftime('%b %Y')} → {row['end'].strftime('%b %Y')} "
                 f"({row['months']} months)<br>"
                 f"Total: ${row['total_cost']:,.0f}<br>"
                 f"Monthly: ${z1:,.0f}")
        fig.add_trace(_cuboid(x0, x1, y0, y1, z0, z1, color,
                              row['notes'] or row['department'], hover))

    # X-axis: monthly tick labels
    month_starts = pd.date_range(start=x_origin, end=x_end_max, freq='MS')
    x_tickvals = [(m - x_origin).days for m in month_starts]
    x_ticktext = [m.strftime('%b %y') for m in month_starts]

    # Y-axis: task labels in order
    y_tickvals = list(range(len(df)))
    y_ticktext = [(r['notes'] or r['department'])[:50] for _, r in df.iterrows()]

    fig.update_layout(
        scene=dict(
            xaxis=dict(title='Time', tickmode='array',
                       tickvals=x_tickvals, ticktext=x_ticktext,
                       tickfont=dict(size=10), showbackground=False),
            yaxis=dict(title='Activity', tickmode='array',
                       tickvals=y_tickvals, ticktext=y_ticktext,
                       autorange='reversed',
                       tickfont=dict(size=10), showbackground=False),
            zaxis=dict(title='Monthly Cost ($)',
                       tickprefix='$', separatethousands=True,
                       tickfont=dict(size=10), showbackground=True,
                       backgroundcolor='#f7f7f7'),
            aspectratio=dict(x=3, y=2, z=1),
            camera=dict(eye=dict(x=1.8, y=-1.8, z=0.9)),
        ),
        height=max(600, ROW_HEIGHT_PX * len(df) + 200),
        margin=dict(l=0, r=0, t=20, b=0),
        showlegend=False,
        paper_bgcolor='white',
    )
    return fig

def render_gantt(df: pd.DataFrame, today: datetime,
                  full_date_range: tuple = None,
                  color_by: str = 'workstream',
                  workstream_filter: str = None):
    """Render the Gantt.  `color_by` selects which column drives the bar
    color — 'workstream' (default) or 'round'.  Pass `full_date_range=(x_min,
    x_max)` to keep column widths the same whether filtered or not."""

    # Use px.timeline — purpose-built Gantt that handles date-typed axes.
    # Bar label = total dollar amount (column H from the sheet).
    import plotly.express as px
    df = df.copy()
    def _fmt_money(v):
        if not v: return ''
        return f'<b>${v:,.0f}</b>'
    df['_cost_label'] = df.get('total_cost', pd.Series([0]*len(df))).apply(_fmt_money)
    if color_by == 'round':
        color_col = 'round'
        color_map = ROUND_COLORS
    else:
        color_col = 'workstream'
        color_map = WORKSTREAM_COLORS
    fig = px.timeline(
        df,
        x_start='start',
        x_end='end',
        y='_label',
        color=color_col,
        color_discrete_map=color_map,
        text='_cost_label',
        pattern_shape='hatched',
        pattern_shape_map={False: '', True: 'x'},
        custom_data=['owner', 'notes', 'department', 'total_cost', 'monthly_cost'],
    )

    # Style the total-cost label overlaid inside each bar, plus rounded corners.
    fig.update_traces(
        textposition='inside',
        insidetextanchor='middle',
        textfont=dict(color='white', size=12),
        marker_cornerradius=8,
        hovertemplate=(
            '<b>%{customdata[1]}</b><br>'
            'Owner: %{customdata[0]}<br>'
            '%{base|%b %Y} → %{x|%b %Y}<br>'
            'Total: $%{customdata[3]:,.0f}<br>'
            'Monthly: $%{customdata[4]:,.0f}<extra></extra>'
        ),
    )

    # Seed-funded bars: same color, overlaid with a light cross-hatch.
    for tr in fig.data:
        if tr.marker.pattern.shape == 'x':
            tr.marker.pattern.update(fillmode='overlay', fgcolor='white',
                                     fgopacity=0.55, size=8, solidity=0.25)

    # (y-axis ordering set later via categoryarray or autorange)

    # X-axis: monthly labels centered between gridlines.
    # Use the FULL date range (not the filtered view's) so column widths
    # stay constant whether filtered or unfiltered.
    if full_date_range:
        x_min, x_max = full_date_range
    else:
        x_min = df['start'].min().replace(day=1)
        x_max = (df['end'].max() + relativedelta(months=1)).replace(day=1)
    month_starts = pd.date_range(start=x_min, end=x_max, freq='MS')

    tickvals, ticktext = [], []
    for i in range(len(month_starts) - 1):
        mid = month_starts[i] + (month_starts[i + 1] - month_starts[i]) / 2
        tickvals.append(mid)
        ticktext.append(f"<b>{month_starts[i].strftime('%b %y')}</b>")

    # Header rows (shown above the calendar header), one value per month:
    #   Monthly Burn     — sum of monthly_cost across tasks active that month
    #   Cumulative Burn  — running total of Monthly Burn
    #   Cum. <group>     — running total for each Chart group (Game
    #                      Development, Go-To-Market)
    def _monthly(rows):
        out = []
        for i in range(len(month_starts) - 1):
            m0, m1 = month_starts[i], month_starts[i + 1]
            out.append(sum(float(r.get('monthly_cost') or 0)
                           for _, r in rows.iterrows()
                           if r['start'] < m1 and r['end'] > m0))
        return out

    def _cumulative(vals):
        out, running = [], 0.0
        for v in vals:
            running += v
            out.append(running)
        return out

    def _k(v, bold=False):
        if not v:
            return ''
        t = f'${v/1000:,.0f}K' if v >= 1000 else f'${v:,.0f}'
        return f'<b>{t}</b>' if bold else t

    monthly_burn_by_month = _monthly(df)
    if workstream_filter == 'Game Development':
        burn_label = 'Game Development Monthly Burn'
        burn_color = WORKSTREAM_COLORS['Game Development']
    elif workstream_filter == 'Go-To-Market':
        burn_label = 'Go-To-Market Monthly Burn'
        burn_color = WORKSTREAM_COLORS['Go-To-Market']
    else:
        burn_label = 'Total Monthly Burn'
        burn_color = '#222'
    header_rows = [  # (label, values, bold, color) — top row first
        (burn_label,   monthly_burn_by_month,           True,  burn_color),
        ('Cumulative', _cumulative(monthly_burn_by_month), False, burn_color),
    ]
    header_yshifts = [HEADER_ROW_BOTTOM + HEADER_ROW_STEP * (len(header_rows) - 1 - k)
                      for k in range(len(header_rows))]

    burn_annotations = []
    header_label_annotations = []
    for (label, vals, bold, color), ys in zip(header_rows, header_yshifts):
        for i, mid in enumerate(tickvals):
            burn_annotations.append(dict(
                x=mid, xref='x', y=1.0, yref='paper', yshift=ys,
                text=_k(vals[i], bold), showarrow=False,
                font=dict(size=11, color=color),
                xanchor='center', yanchor='middle',
            ))
        header_label_annotations.append(dict(
            x=0, xref='paper', y=1.0, yref='paper', yshift=ys,
            text=f'<b>{label}:</b>', showarrow=False,
            font=dict(size=11, color=color if color != '#222' else '#555'),
            xanchor='right', yanchor='middle', xshift=-10,
        ))

    fig.update_xaxes(
        type='date',
        range=[x_min, x_max],
        tickmode='array',
        tickvals=tickvals,
        ticktext=ticktext,
        side='top',
        showgrid=False,                  # we draw gridlines as shapes below
        showline=False,
        ticks='',
        tickfont=dict(size=11, color='#555'),
    )
    # Milestone months: shaded column through the chart, label above the
    # Monthly Burn row.  (Appended to burn_annotations — update_layout below
    # replaces any annotations added directly to the figure.)
    plot_h = ROW_HEIGHT_PX * df['_label'].nunique() + 10       # height minus top/bottom margins
    for m_start, m_label, full in MILESTONES:
        if not (x_min <= m_start < x_max):
            continue
        m_end = m_start + relativedelta(months=1)
        y_top = (1 + (MILESTONE_LABEL_YSHIFT + MILESTONE_LABEL_HEIGHT) / plot_h
                 if full else 1)
        fig.add_shape(
            type='rect', x0=m_start, x1=m_end, y0=0, y1=y_top, yref='paper',
            fillcolor='rgba(201,162,39,0.18)', line_width=0, layer='below',
        )
        burn_annotations.append(dict(
            x=m_start + (m_end - m_start) / 2, xref='x',
            y=1.0, yref='paper', yanchor='bottom', yshift=MILESTONE_LABEL_YSHIFT,
            text='<b>' + m_label.upper().replace(' ', '<br>') + '</b>',
            showarrow=False,
            font=dict(size=11, color='#8A6D12'),
            bgcolor=None if full else 'rgba(201,162,39,0.18)', borderpad=3,
        ))

    # Month-boundary vertical gridlines (drawn as shapes so they sit between
    # the labels, not under them).
    for ms in month_starts:
        fig.add_shape(
            type='line',
            x0=ms, x1=ms,
            y0=0, y1=1, yref='paper',
            line=dict(color='#eee', width=1),
            layer='below',
        )
    fig.update_yaxes(
        autorange='reversed',
        showgrid=False,
        title_text='',
        tickfont=dict(size=12, color='#222'),
    )

    # Fixed row height — each visible row is ROW_HEIGHT_PX pixels tall.
    # Chart total height = rows × pixel/row + top/bottom margins (extra
    # top room for the monthly-burn header row).
    n_rows = df['_label'].nunique()
    fig.update_layout(
        height=ROW_HEIGHT_PX * n_rows + HEADER_TOP_MARGIN + 50,
        margin=dict(l=20, r=40, t=HEADER_TOP_MARGIN, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
        showlegend=False,
        bargap=0.30,
        annotations=burn_annotations + header_label_annotations,
    )
    return fig


# ── UI ──────────────────────────────────────────────────────────────────────
st.markdown(
    "<h2 style='margin-bottom:0;'>Long Zhu — Workstream Timeline</h2>",
    unsafe_allow_html=True,
)

# Match font family of filter pills + legend to the chart's axis labels
# (Plotly's default font stack).
st.markdown(
    """
    <style>
    div[data-testid="stPills"] button,
    div[data-testid="stMultiSelect"] *,
    .stPills label,
    .stMultiSelect label,
    .lz-legend {
        font-family: "Open Sans", verdana, arial, sans-serif !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.write('')

# Refresh button — clears the 5-min cache and re-pulls from the sheet.
col_title, col_refresh = st.columns([6, 1])
with col_refresh:
    if st.button('🔄 Refresh', help='Re-read the Google Sheet now',
                  use_container_width=True):
        load_tasks.clear()

# Load tasks early so the owner filter knows what options to show
try:
    df = prepare_df(load_tasks())
except Exception as e:
    st.error(f'Could not load Long Zhu Budget sheet: {e}')
    st.stop()

# Filter row: Workstream pills + Owner multi-select (Color-by below)
col_stream, col_owner = st.columns([3, 2])
with col_stream:
    filter_choice = st.pills(
        'Filter by Workstream:',
        list(FILTER_TO_WS.keys()),
        default='All',
        label_visibility='visible',
    )
    if filter_choice is None:
        filter_choice = 'All'
    color_by_choice = st.radio(
        'Color by:',
        options=['Stream', 'Round'],
        index=0,
        label_visibility='visible',
        horizontal=True,
    )

with col_owner:
    all_owners = sorted({o for o in df['owner'].dropna().unique() if o})
    selected_owners = st.multiselect(
        'Filter by owner:',
        options=all_owners,
        default=[],
        placeholder='All owners',
        label_visibility='visible',
    )

# Legend pills (visual reference, not interactive) — driven by color choice
if color_by_choice == 'Round':
    # Show only rounds actually present in the data, in a stable order
    present = [r for r in ROUND_COLORS if r in set(df['round'].dropna())]
    legend_items = [(r, ROUND_COLORS[r]) for r in present]
else:
    legend_items = [(g, WORKSTREAM_COLORS.get(g, '#6E7479'))
                    for g in dict.fromkeys(df['workstream'])]

_legend_row = ('<div class="lz-legend" style="display:flex; gap:18px; align-items:center; '
               'font-size:13px; color:#444; margin-top:6px; margin-bottom:{mb}px;">')
_swatch = ('<span style="display:inline-flex;align-items:center;gap:6px;">'
           '<span style="display:inline-block;width:{w}px;height:10px;border-radius:2px;'
           'background:{bg};"></span>{label}</span>')
_hatch = ('repeating-linear-gradient(45deg,rgba(255,255,255,.55) 0 1px,transparent 1px 5px),'
          'repeating-linear-gradient(-45deg,rgba(255,255,255,.55) 0 1px,transparent 1px 5px),#777')

# Row 1: Chart groups (or rounds).  Row 2: solid vs cross-hatched funding.
legend_html = _legend_row.format(mb=2)
for label, color in legend_items:
    legend_html += _swatch.format(w=10, bg=color, label=label)
legend_html += '</div>' + _legend_row.format(mb=14)
legend_html += _swatch.format(w=18, bg='#777', label='Pre-Seed')
legend_html += _swatch.format(w=18, bg=_hatch, label='Seed | Launch')
legend_html += '</div>'
st.markdown(legend_html, unsafe_allow_html=True)

# Apply filters — remove non-matching rows so each visible row keeps the
# same fixed pixel height.  Column widths stay constant because we pass the
# full unfiltered date range to render_gantt below.
selected_ws = FILTER_TO_WS.get(filter_choice)
df_view = df.copy()
if selected_ws:
    df_view = df_view[df_view['workstream'] == selected_ws]
if selected_owners:
    df_view = df_view[df_view['owner'].isin(selected_owners)]

if df_view.empty:
    st.warning('No tasks matched the current filters.')
    st.stop()

# Lock the x-axis date range to the FULL dataset so month columns stay the
# same pixel width whether filtered or unfiltered.
full_x_min = df['start'].min().replace(day=1)
full_x_max = (df['end'].max() + relativedelta(months=1)).replace(day=1)

fig = render_gantt(
    df_view, today=datetime.now(),
    full_date_range=(full_x_min, full_x_max),
    color_by='round' if color_by_choice == 'Round' else 'workstream',
    workstream_filter=selected_ws,
)
st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

st.caption(f"{df_view['_label'].nunique()} active task(s) shown · "
            f'Source: Long Zhu Budget Google Sheet (auto-refreshes every 5 min)')
