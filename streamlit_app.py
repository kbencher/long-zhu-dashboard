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
    '_hidden':          'rgba(0,0,0,0)',   # filtered-out rows
}
FILTER_TO_WS = {
    'All':         None,
    'Game Development': 'Game Development',
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
    """Preserve the order tasks appear in the Google Sheet and build unique
    y-axis labels.  Run this once on the *full* dataset before any filtering
    so label identities stay stable across the unfiltered / filtered views."""
    df = df.copy().reset_index(drop=True)
    df['_label'] = df.apply(_task_label, axis=1)
    # Make labels unique (Plotly's category axis dedupes identical labels).
    df['_label'] = [f"{lbl}<span style='display:none'>{i}</span>"
                     for i, lbl in enumerate(df['_label'])]
    df['_color'] = df['workstream'].map(WORKSTREAM_COLORS)
    return df


ROW_HEIGHT_PX = 50      # fixed height per task row

def render_gantt(df: pd.DataFrame, today: datetime,
                  full_date_range: tuple = None):
    """Render the Gantt.  Pass `full_date_range=(x_min, x_max)` to keep
    column (month) widths the same whether filtered or not."""

    # Use px.timeline — purpose-built Gantt that handles date-typed axes.
    # Pass `text='owner'` directly so each bar gets the right row's owner
    # (px.timeline groups bars by color into traces; setting text via
    # update_traces() can mis-align across trace groups).
    import plotly.express as px
    df = df.copy()
    df['_owner_bold'] = df['owner'].apply(lambda o: f'<b>{o}</b>' if o else '')
    fig = px.timeline(
        df,
        x_start='start',
        x_end='end',
        y='_label',
        color='workstream',
        color_discrete_map=WORKSTREAM_COLORS,
        text='_owner_bold',
        custom_data=['owner', 'notes', 'department'],
    )

    # Style the owner label overlaid inside each bar, plus rounded corners.
    fig.update_traces(
        textposition='inside',
        insidetextanchor='middle',
        textfont=dict(color='white', size=12, family='Inter'),
        marker_cornerradius=8,
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
    fig.update_yaxes(
        autorange='reversed',
        showgrid=False,
        title_text='Activity',
        title_font=dict(size=13, color='#222'),
        tickfont=dict(size=12, color='#222'),
    )

    # Fixed row height — each visible row is ROW_HEIGHT_PX pixels tall.
    # Chart total height = rows × pixel/row + top/bottom margins.
    n_rows = len(df)
    fig.update_layout(
        height=ROW_HEIGHT_PX * n_rows + 110,
        margin=dict(l=20, r=40, t=60, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
        showlegend=False,
        bargap=0.30,
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
    '<div class="lz-legend" style="display:flex; gap:18px; align-items:center; '
    'font-size:13px; color:#444; margin-top:6px; margin-bottom:14px;">'
)
for label, color in [
    ('Game Development', WORKSTREAM_COLORS['Game Development']),
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
)
st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

st.caption(f'{len(df_view)} active task(s) shown · '
            f'Source: Long Zhu Budget Google Sheet (auto-refreshes every 5 min)')
