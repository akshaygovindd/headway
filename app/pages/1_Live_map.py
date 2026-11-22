"""Live map: subway vehicles on the official line colours."""

import pydeck as pdk
import streamlit as st

import ui
from lib.freshness import format_age, is_snapshot_stale, snapshot_age
from lib.livemap import route_paths, vehicles_frame

ui.page_header("Live map")
base = ui.data_base()
manifest = ui.gold_manifest(base)
snapshot = ui.live_snapshot(base)
ui.show_as_of_and_freshness(manifest, snapshot)

age = snapshot_age(snapshot)
if is_snapshot_stale(age):
    st.warning(
        f"The live snapshot is {format_age(age).replace(' ago', '')} old; positions may be out of date."
    )

vehicles = vehicles_frame(snapshot, ui.gold_table(base, "dim_stop"))
paths = route_paths(ui.gold_table(base, "route_shapes"))
dark = st.toggle("Dark basemap", value=False)

fresh = vehicles[~vehicles["is_stale"]]
stale = vehicles[vehicles["is_stale"]]
layers = [
    pdk.Layer(
        "PathLayer",
        data=paths,
        get_path="path",
        get_color="color",
        width_min_pixels=3,
        pickable=False,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        data=fresh,
        get_position="[lon, lat]",
        get_fill_color="color",
        get_line_color=[255, 255, 255],
        stroked=True,
        line_width_min_pixels=1,
        get_radius=60,
        radius_min_pixels=5,
        pickable=True,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        data=stale,
        get_position="[lon, lat]",
        get_line_color="color",
        stroked=True,
        filled=False,
        line_width_min_pixels=2,
        get_radius=60,
        radius_min_pixels=5,
        pickable=True,
    ),
]
deck = pdk.Deck(
    layers=layers,
    initial_view_state=pdk.ViewState(latitude=42.36, longitude=-71.08, zoom=10.5),
    map_provider="carto",
    map_style="dark" if dark else "light",
    tooltip={
        "html": "<b>{line}</b><br/>Trip {trip}<br/>Next stop: {next_stop}<br/>Position age: {position_age}"
    },
)
st.pydeck_chart(deck, width="stretch")
st.caption(
    f"{len(vehicles)} vehicles ({len(stale)} with stale positions, drawn hollow). "
    "Basemap © CARTO, map data © OpenStreetMap contributors."
)
