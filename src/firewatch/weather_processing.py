"""ERA5-Land daily UTC summaries with correct cumulative precipitation handling."""
import numpy as np
import pandas as pd
import xarray as xr

def summarise_dataset(ds, grid):
    if not {"tp", "t2m", "swvl1"} <= set(ds.data_vars):
        raise ValueError("ERA5 variables tp/t2m/swvl1 are required")
    if ds.tp.attrs.get("units") not in {"m", "m of water equivalent"}:
        raise ValueError("Unexpected ERA5 precipitation unit")
    time_name = "valid_time" if "valid_time" in ds.coords else "time"
    times = pd.DatetimeIndex(ds[time_name].values)
    if times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError("ERA5 hourly timestamps are not unique and ordered")
    index = {"dims": "grid", "coords": {"grid": grid["grid_id"].to_numpy()}}
    sampled = ds[["tp", "t2m", "swvl1"]].sel(
        latitude=xr.DataArray(grid["centroid_lat"].to_numpy(), **index),
        longitude=xr.DataArray(grid["centroid_lon"].to_numpy(), **index),
        method="nearest",
    )
    temperature = (sampled.t2m - 273.15).resample({time_name: "1D"})
    moisture = sampled.swvl1.resample({time_name: "1D"})
    thermal = xr.Dataset({
        "temperature_mean_c": temperature.mean().where(temperature.count() == 24),
        "temperature_max_c": temperature.max().where(temperature.count() == 24),
        "soil_moisture": moisture.mean().where(moisture.count() == 24),
    }).to_dataframe().reset_index().rename(columns={time_name: "date", "grid": "grid_id"})
    midnight = sampled.tp.isel({time_name: np.flatnonzero(times.hour == 0)})
    rain = (midnight * 1000).rename("rainfall_mm").to_dataframe().reset_index().rename(columns={time_name: "date", "grid": "grid_id"})
    rain["date"] = pd.to_datetime(rain["date"]) - pd.Timedelta(days=1)
    result = thermal[["date", "grid_id", "temperature_mean_c", "temperature_max_c", "soil_moisture"]].merge(rain[["date", "grid_id", "rainfall_mm"]], on=["date", "grid_id"], how="outer")
    if (result["rainfall_mm"].dropna() < -0.001).any():
        raise ValueError("Negative daily precipitation")
    result["rainfall_mm"] = result["rainfall_mm"].clip(lower=0)
    ds.close()
    return result
