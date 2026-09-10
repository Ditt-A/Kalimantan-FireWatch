"""Interactive Kalimantan FireWatch dashboard.

Run: .venv/bin/streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import altair as alt
import geopandas as gpd
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).resolve().parent
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
PRIORITY_ORDER = {"Low": 0, "Watch": 1, "High": 2, "Critical": 3}

st.set_page_config(page_title="Kalimantan FireWatch", page_icon="🔥", layout="wide")


@st.cache_data(show_spinner="Loading analytical tables…")
def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, gpd.GeoDataFrame]:
    fact = pd.read_parquet(PROCESSED / "fact_grid_week.parquet")
    fact["week_start"] = pd.to_datetime(fact["week_start"])
    grid = gpd.read_parquet(PROCESSED / "dim_grid.parquet")
    prediction_path = PROCESSED / "prediction_grid_week.parquet"
    prediction = pd.read_parquet(prediction_path) if prediction_path.exists() else pd.DataFrame()
    if not prediction.empty:
        prediction["week_start"] = pd.to_datetime(prediction["week_start"])
    return fact, prediction, grid


def colour_scale(series: pd.Series, categorical: bool) -> list[list[int]]:
    if categorical:
        colours = {
            "Low": [75, 135, 185, 185], "Watch": [246, 183, 0, 190],
            "High": [234, 111, 45, 205], "Critical": [178, 35, 55, 220],
            "Moderate": [246, 183, 0, 190], "Very High": [178, 35, 55, 220],
            "Medium": [234, 111, 45, 205], "Zero/Low": [75, 135, 185, 185],
        }
        return [colours.get(str(value), [120, 120, 120, 160]) for value in series]
    values = pd.to_numeric(series, errors="coerce").fillna(0)
    lower, upper = values.quantile(0.05), values.quantile(0.95)
    scaled = np.zeros(len(values)) if upper <= lower else np.clip((values - lower) / (upper - lower), 0, 1)
    return [[int(45 + 205 * value), int(145 - 80 * value), int(195 - 130 * value), 190] for value in scaled]


def draw_map(frame: gpd.GeoDataFrame, metric: str, label: str, categorical: bool = False) -> None:
    display = frame.copy()
    display["metric_value"] = display[metric].fillna(0)
    display["fill_colour"] = colour_scale(display["metric_value"], categorical)
    display["tooltip_value"] = display["metric_value"].map(
        lambda value: f"{value:.3f}" if isinstance(value, (float, int, np.number)) else str(value)
    )
    layer = pdk.Layer(
        "GeoJsonLayer", display.__geo_interface__, pickable=True, stroked=True, filled=True,
        get_fill_color="properties.fill_colour", get_line_color=[55, 55, 55, 65],
        line_width_min_pixels=0.3,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=pdk.ViewState(latitude=0.2, longitude=114.4, zoom=4.5),
            tooltip={"html": f"<b>{{grid_id}}</b><br/>{{province}} — {{district}}<br/>{label}: {{tooltip_value}}<br/>Peat: {{peat_pct}}%"},
            map_style="mapbox://styles/mapbox/light-v11",
        ),
        use_container_width=True,
    )


def filter_fact(fact: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    with st.sidebar:
        st.header("Filters")
        years = sorted(fact["week_start"].dt.year.unique())
        year = st.selectbox("Year", years, index=len(years) - 1)
        month = st.selectbox("Month", ["All"] + list(range(1, 13)), format_func=lambda value: "All" if value == "All" else pd.Timestamp(2000, value, 1).strftime("%B"))
        provinces = ["All"] + sorted(fact["province"].unique())
        province = st.selectbox("Province", provinces)
        district_source = fact if province == "All" else fact.loc[fact["province"].eq(province)]
        district = st.selectbox("District", ["All"] + sorted(district_source["district"].unique()))
        confidence = st.selectbox("Confidence", ["All", "High only"])
        st.selectbox("Satellite", ["VIIRS NOAA-20"], disabled=True)
        st.caption("FIRMS rows are satellite active-fire detections, not independently verified fire events.")
    result = fact.loc[fact["week_start"].dt.year.eq(year)].copy()
    if month != "All":
        result = result.loc[result["week_start"].dt.month.eq(month)]
    if province != "All":
        result = result.loc[result["province"].eq(province)]
    if district != "All":
        result = result.loc[result["district"].eq(district)]
    return result, confidence


def aggregate_historical(data: pd.DataFrame, confidence: str) -> pd.DataFrame:
    count = "high_confidence_count" if confidence == "High only" else "hotspot_count"
    output = data.groupby("grid_id", as_index=False).agg(
        hotspot_count=(count, "sum"), frp_sum=("frp_sum", "sum"), area_km2=("area_km2", "first"),
        peat_pct=("peat_pct", "first"), province=("province", "first"), district=("district", "first"),
    )
    output["hotspot_density_per_1000km2"] = output["hotspot_count"] / output["area_km2"] * 1000
    return output


def support_chart(mode: str, fact: pd.DataFrame, prediction: pd.DataFrame, confidence: str) -> None:
    st.subheader("Supporting analysis")
    if mode == "Historical":
        count = "high_confidence_count" if confidence == "High only" else "hotspot_count"
        monthly = fact.assign(month=fact["week_start"].dt.to_period("M").dt.to_timestamp()).groupby("month", as_index=False)[count].sum()
        st.altair_chart(alt.Chart(monthly).mark_line(point=True).encode(
            x=alt.X("month:T", title="Month"), y=alt.Y(f"{count}:Q", title="Hotspot detections"),
            tooltip=["month:T", f"{count}:Q"],
        ).properties(height=280), use_container_width=True)
    elif mode == "Environment":
        chart = alt.Chart(fact.dropna(subset=["rainfall_30d"])).mark_circle(opacity=0.35).encode(
            x=alt.X("rainfall_30d:Q", title="Rainfall in previous 30 days (mm)"),
            y=alt.Y("hotspot_count:Q", title="Weekly hotspot detections"),
            color=alt.Color("province:N", legend=None),
            tooltip=["province:N", "district:N", "rainfall_30d:Q", "hotspot_count:Q"],
        ).properties(height=300)
        st.altair_chart(chart, use_container_width=True)
    elif mode == "Fire Risk":
        path = REPORTS / "classification_feature_importance.csv"
        if path.exists():
            importance = pd.read_csv(path).head(12)
            st.altair_chart(alt.Chart(importance).mark_bar().encode(
                x=alt.X("importance:Q", title="Importance"), y=alt.Y("feature:N", sort="-x", title=None),
                tooltip=["feature:N", "importance:Q"],
            ).properties(height=320), use_container_width=True)
        else:
            st.info("Train models to display feature importance.")
    elif mode == "Expected Count" and not prediction.empty:
        timeline = prediction.groupby("week_start", as_index=False).agg(actual=("hotspot_next_7d", "sum"), predicted=("predicted_hotspot_count_next_7d", "sum")).melt("week_start", var_name="series", value_name="detections")
        st.altair_chart(alt.Chart(timeline).mark_line(point=True).encode(
            x="week_start:T", y="detections:Q", color=alt.Color("series:N", title=None),
            tooltip=["week_start:T", "series:N", "detections:Q"],
        ).properties(height=300), use_container_width=True)
    elif mode == "Priority" and not prediction.empty:
        latest = prediction.loc[prediction["week_start"].eq(prediction["week_start"].max())]
        st.altair_chart(alt.Chart(latest).mark_circle(opacity=0.6).encode(
            x=alt.X("predicted_fire_probability:Q", title="Predicted fire probability"),
            y=alt.Y("predicted_hotspot_count_next_7d:Q", title="Expected hotspot count"),
            color=alt.Color("priority_category:N", title="Priority"),
            tooltip=["grid_id:N", "district:N", "priority_category:N", "predicted_fire_probability:Q", "predicted_hotspot_count_next_7d:Q"],
        ).properties(height=300), use_container_width=True)


def main() -> None:
    st.title("🔥 Kalimantan FireWatch")
    st.caption("Spatio-temporal fire monitoring and predictive decision support for Kalimantan.")
    if not (PROCESSED / "fact_grid_week.parquet").exists():
        st.error("Processed data is unavailable. Run scripts/build_dataset.py first.")
        st.stop()
    fact, prediction, grid = load_tables()
    selected, confidence = filter_fact(fact)
    mode = st.radio("Dashboard mode", ["Historical", "Environment", "Fire Risk", "Expected Count", "Priority"], horizontal=True)

    if mode == "Historical":
        map_option = st.radio("Map metric", ["Hotspot density", "Hotspot count", "FRP"], horizontal=True)
        data = aggregate_historical(selected, confidence)
        metric = {"Hotspot density": "hotspot_density_per_1000km2", "Hotspot count": "hotspot_count", "FRP": "frp_sum"}[map_option]
        map_data = grid.merge(data, on="grid_id", how="inner", suffixes=("", "_data"))
        total = data["hotspot_count"].sum()
        values = [("Total detections", f"{total:,.0f}"), ("High-confidence rate", f"{selected['high_confidence_count'].sum() / selected['hotspot_count'].sum() * 100:.1f}%" if selected["hotspot_count"].sum() else "—"), ("Average FRP", f"{selected['frp_sum'].sum() / selected['hotspot_count'].sum():.2f} MW" if selected["hotspot_count"].sum() else "—"), ("Peak week", str(selected.groupby("week_start")["hotspot_count"].sum().idxmax().date()))]
        label, categorical = map_option, False
    elif mode == "Environment":
        option = st.radio("Map metric", ["Rainfall 30D", "Soil moisture", "Peatland share"], horizontal=True)
        metric = {"Rainfall 30D": "rainfall_30d", "Soil moisture": "soil_moisture_7d", "Peatland share": "peat_pct"}[option]
        data = selected.groupby("grid_id", as_index=False).agg(**{metric: (metric, "mean")}, peat_pct=("peat_pct", "first"), province=("province", "first"), district=("district", "first"))
        map_data = grid.merge(data, on="grid_id", how="inner", suffixes=("", "_data"))
        values = [("Average rainfall 30D", f"{selected['rainfall_30d'].mean():.1f} mm"), ("Average soil moisture", f"{selected['soil_moisture_7d'].mean():.3f}"), ("Peat grid share", f"{selected['peat_pct'].gt(0).mean() * 100:.1f}%"), ("Selected weeks", selected["week_start"].nunique())]
        label, categorical = option, False
    else:
        view = prediction.merge(selected[["grid_id", "week_start"]], on=["grid_id", "week_start"], how="inner") if not prediction.empty else pd.DataFrame()
        if view.empty:
            st.warning("No out-of-sample model output matches this filter. Select a 2026 period after training.")
            st.stop()
        latest = view.loc[view["week_start"].eq(view["week_start"].max())].copy()
        if mode == "Fire Risk":
            metric, label, categorical = "predicted_fire_probability", "Predicted fire probability", False
            values = [("Prediction week", str(latest["week_start"].iloc[0].date())), ("Mean probability", f"{latest[metric].mean():.1%}"), ("High-risk grids", int(latest["risk_category"].isin(["High", "Very High"]).sum())), ("Top district", latest.loc[latest[metric].idxmax(), "district"])]
        elif mode == "Expected Count":
            metric, label, categorical = "predicted_hotspot_count_next_7d", "Expected hotspot detections", False
            values = [("Prediction week", str(latest["week_start"].iloc[0].date())), ("Expected detections", f"{latest[metric].sum():.1f}"), ("Observed detections", int(latest["hotspot_next_7d"].sum())), ("Top grid", latest.loc[latest[metric].idxmax(), "grid_id"])]
        else:
            metric, label, categorical = "priority_category", "Monitoring priority", True
            values = [("Prediction week", str(latest["week_start"].iloc[0].date())), ("Critical grids", int(latest[metric].eq("Critical").sum())), ("Critical peat grids", int(latest.loc[latest["peat_pct"].gt(0), metric].eq("Critical").sum())), ("Top province", latest.groupby("province")[metric].apply(lambda s: s.map(PRIORITY_ORDER).sum()).idxmax())]
        map_data = grid.merge(latest, on="grid_id", how="inner", suffixes=("", "_data"))

    for column, (title, value) in zip(st.columns(4), values):
        column.metric(title, value)
    draw_map(map_data, metric, label, categorical)
    support_chart(mode, selected, prediction, confidence)


if __name__ == "__main__":
    main()
