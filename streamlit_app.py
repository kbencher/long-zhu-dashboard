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
WORKSTREAM_COLORS = {
    'Game Development': '#2D6A3F',
    'Testing':          '#A03D2D',
    'Marketing':        '#1F5A8C',
    'Community':        '#5B47B0',
    'Sales & Ops':      '#6E7479',
}
FILTER_TO_WS = {
    'All':         None,
    'Game dev':    'Game Development',
    'Testing':     'Testing',
    'Marketing':   'Marketing',
    'Community':   'Community',
    'Sales & Ops': 'Sales & Ops',
}


# ── Data ────────────────────────────────────────────────────────────────────
SHEET_KEY = '1rKFY6S-VZFnOkZLs_JeNtZSkFZIkyVbSROSrmx0rb40'


TASKS_TAB_GID = 1610950122   # 'Copy of Sheet1' — clean Gantt-input layout


@st.cache_data(ttl=300)
def load_tasks() -> pd.DataFrame:
    """Pull tasks from the Gantt-input tab of the Long Zhu Budget sheet.

    Layout (row 5 is the header):
        A  (blank)
        B  Stream         — workstream (Game Development, Testing, Marketing,
                            Community, Sales & Ops)
        C  Owner
        D  Notes          — task description shown on the row label
        E  Start Date     — mm/dd/yy
        F  Months         — integer duration
    Section header rows (e.g. 'GAME DEVELOPMENT' in CAPS) and rows missing
    Start Date or Months are skipped.
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
    rows = ws.get('A1:F100', value_render_option='FORMATTED_VALUE')

    def _parse_date(s):
        if not s:
            return None
        for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(str(s).strip(), fmt)
            except ValueError:
                pass
        return None

    out = []
    for r in rows:
        if not r or len(r) < 6:
            continue
        stream = str(r[1] or '').strip() if len(r) > 1 else ''
        owner  = str(r[2] or '').strip() if len(r) > 2 else ''
        notes  = str(r[3] or '').strip() if len(r) > 3 else ''
        start  = _parse_date(r[4]) if len(r) > 4 else None
        months_raw = str(r[5] or '').strip() if len(r) > 5 else ''

        if not start or not months_raw:
            continue
        try:
            n_months = int(float(months_raw.replace(',', '')))
        except (ValueError, AttributeError):
            continue
        end = start + relativedelta(months=n_months)

        bucket = _bucket_workstream(stream, '')
        out.append({
            'workstream': bucket,
            'sub':        stream,
            'department': stream,
            'owner':      owner or 'TBD',
            'notes':      notes,
            'start':      start,
            'end':        end,
        })
    return pd.DataFrame(out)


def _bucket_workstream(ws_group: str, dept: str) -> str:
    g = (ws_group or '').lower()
    d = (dept or '').lower()
    if 'game' in g or 'identity' in g or 'illustration' in g or 'design' in g \
            or 'story' in g or 'tournament' in g:
        return 'Game Development'
    if 'testing' in g:
        return 'Testing'
    if 'marketing' in g:
        return 'Marketing'
    if 'community' in g or 'community' in d:
        return 'Community'
    if 'sales' in g or 'distribution' in g or 'admin' in g or 'monthly' in g:
        return 'Sales & Ops'
    return 'Sales & Ops'


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
    """Sort by workstream order + start date, and build unique y-axis labels.
    Run this once on the *full* dataset before any filtering so that label
    identities stay stable across the unfiltered / filtered views."""
    ws_order = ['Game Development', 'Testing', 'Marketing', 'Community', 'Sales & Ops']
    df = df.copy()
    df['_ws_rank'] = df['workstream'].map({w: i for i, w in enumerate(ws_order)})
    df = df.sort_values(['_ws_rank', 'start']).reset_index(drop=True)
    df['_label'] = df.apply(_task_label, axis=1)
    # Make labels unique (Plotly's category axis dedupes identical labels).
    df['_label'] = [f"{lbl}<span style='display:none'>{i}</span>"
                     for i, lbl in enumerate(df['_label'])]
    df['_color'] = df['workstream'].map(WORKSTREAM_COLORS)
    return df


def render_gantt(df: pd.DataFrame, today: datetime,
                  all_labels: list = None, total_rows: int = None):
    """Render the Gantt.  Pass `all_labels` to lock the y-axis to the full
    set of categories (so filtered views still show every row position)."""

    # Use px.timeline — purpose-built Gantt that handles date-typed axes.
    # Pass `text='owner'` directly so each bar gets the right row's owner
    # (px.timeline groups bars by color into traces; setting text via
    # update_traces() can mis-align across trace groups).
    import plotly.express as px
    fig = px.timeline(
        df,
        x_start='start',
        x_end='end',
        y='_label',
        color='workstream',
        color_discrete_map=WORKSTREAM_COLORS,
        text='owner',
        custom_data=['owner', 'notes', 'department'],
    )

    # Style the owner label overlaid inside each bar
    fig.update_traces(
        textposition='inside',
        insidetextanchor='middle',
        textfont=dict(color='white', size=11, family='Inter'),
        hovertemplate=(
            '<b>%{customdata[1]}</b><br>'
            'Owner: %{customdata[0]}<br>'
            '%{base|%b %Y} → %{x|%b %Y}<extra></extra>'
        ),
    )

    # (y-axis ordering set later via categoryarray or autorange)

    # Vertical "Today" marker
    fig.add_vline(
        x=today,
        line=dict(color='#e74c3c', width=2),
    )

    # X-axis: monthly labels centered between gridlines.
    # Strategy: place tick LABELS at mid-month, draw GRIDLINES manually at
    # month boundaries so the date hovers in the middle of its column.
    x_min = df['start'].min().replace(day=1)
    x_max = (df['end'].max() + relativedelta(months=1)).replace(day=1)
    month_starts = pd.date_range(start=x_min, end=x_max, freq='MS')

    tickvals, ticktext = [], []
    for i in range(len(month_starts) - 1):
        mid = month_starts[i] + (month_starts[i + 1] - month_starts[i]) / 2
        tickvals.append(mid)
        ticktext.append(month_starts[i].strftime('%b %y'))

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
    yaxis_kwargs = dict(
        showgrid=False,
        title_text='Activity',
        title_font=dict(size=13, color='#222', family='Inter'),
        tickfont=dict(size=12, color='#222'),
    )
    if all_labels:
        # Lock the y-axis to the full task list so filtered views still
        # show every row position.  Reverse so first task is at top.
        yaxis_kwargs['categoryorder'] = 'array'
        yaxis_kwargs['categoryarray'] = list(reversed(all_labels))
    else:
        yaxis_kwargs['autorange'] = 'reversed'
    fig.update_yaxes(**yaxis_kwargs)

    # Keep chart height constant even when filters reduce the visible rows.
    # If `total_rows` is supplied, size the canvas to that — bars get a bit
    # thicker when fewer rows are visible, but the chart doesn't shrink.
    sizing_rows = total_rows if total_rows is not None else len(df)
    fig.update_layout(
        height=max(440, 38 * sizing_rows + 90),
        margin=dict(l=20, r=40, t=60, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
        showlegend=False,
        bargap=0.35,
    )
    return fig


# ── UI ──────────────────────────────────────────────────────────────────────
st.markdown(
    "<h2 style='margin-bottom:0;'>Long Zhu — Workstream Timeline</h2>",
    unsafe_allow_html=True,
)
st.write('')

# Load tasks early so the owner filter knows what options to show
try:
    df = prepare_df(load_tasks())
except Exception as e:
    st.error(f'Could not load Long Zhu Budget sheet: {e}')
    st.stop()

# Filter row: Stream pills + Owner multi-select
col_stream, col_owner = st.columns([3, 2])
with col_stream:
    filter_choice = st.pills(
        'Filter by stream:',
        list(FILTER_TO_WS.keys()),
        default='All',
        label_visibility='visible',
    )
    if filter_choice is None:
        filter_choice = 'All'

with col_owner:
    all_owners = sorted({o for o in df['owner'].dropna().unique() if o})
    selected_owners = st.multiselect(
        'Filter by owner:',
        options=all_owners,
        default=[],
        placeholder='All owners',
        label_visibility='visible',
    )

# Legend pills (visual reference, not interactive)
legend_html = (
    '<div style="display:flex; gap:18px; align-items:center; '
    'font-size:13px; color:#444; margin-top:6px; margin-bottom:14px;">'
)
for label, color in [
    ('Game development', WORKSTREAM_COLORS['Game Development']),
    ('Testing',          WORKSTREAM_COLORS['Testing']),
    ('Marketing',        WORKSTREAM_COLORS['Marketing']),
    ('Community',        WORKSTREAM_COLORS['Community']),
    ('Sales & Ops',      WORKSTREAM_COLORS['Sales & Ops']),
]:
    legend_html += (
        f'<span style="display:inline-flex;align-items:center;gap:6px;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
        f'background:{color};"></span>{label}</span>'
    )
legend_html += (
    '<span style="display:inline-flex;align-items:center;gap:6px;">'
    '<span style="display:inline-block;width:2px;height:14px;background:#e74c3c;"></span>'
    'Today</span></div>'
)
st.markdown(legend_html, unsafe_allow_html=True)

# Apply filters
selected_ws = FILTER_TO_WS.get(filter_choice)
df_view = df.copy()
if selected_ws:
    df_view = df_view[df_view['workstream'] == selected_ws]
if selected_owners:
    df_view = df_view[df_view['owner'].isin(selected_owners)]

if df_view.empty:
    st.warning('No tasks matched the current filters.')
    st.stop()

fig = render_gantt(
    df_view, today=datetime.now(),
    all_labels=df['_label'].tolist(),   # lock y-axis to full set
    total_rows=len(df),
)
st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

st.caption(f'{len(df_view)} active task(s) shown · '
            f'Source: Long Zhu Budget Google Sheet (auto-refreshes every 5 min)')
