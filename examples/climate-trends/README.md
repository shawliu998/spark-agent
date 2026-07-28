# Climate trends — a non-bio example project

Real global temperature data, small enough to analyze in seconds, rich enough
for a genuine end-to-end workflow: trend estimation, decadal comparison, a
publication-quality figure, and a report with every number traced to code.

## Data

`data/gistemp_global_means.csv` — the unmodified **NASA GISS Surface Temperature
Analysis (GISTEMP v4)** Land-Ocean Temperature Index, global means.

- Values are temperature **anomalies in °C relative to the 1951–1980 mean**.
- Columns: `Year`, monthly anomalies (`Jan`…`Dec`), annual mean `J-D`,
  December-to-November mean `D-N`, and seasonal means (`DJF`, `MAM`, `JJA`,
  `SON`). Missing values are `***`. Note the file's first line is a title —
  the header is on line 2 (`skiprows=1` in pandas).
- Source: <https://data.giss.nasa.gov/gistemp/> (retrieved 2026-07-03; NASA
  data, public domain). Cite as: GISTEMP Team, GISS Surface Temperature
  Analysis (GISTEMP), version 4. NASA Goddard Institute for Space Studies.

`data/gistemp_annual_global_means.csv` — the Core-ready annual analysis input.

- It contains only `Year` and the annual `J-D` anomaly so its first row is a
  CSV header and both columns profile as numeric without parser options.
- It is deterministically derived from the bundled raw NASA file by
  `scripts/prepare_analysis_csv.py`: rows whose annual `J-D` value is `***`
  are omitted (currently the incomplete 2026 row); no other values are
  changed. Run `python3 scripts/prepare_analysis_csv.py --check` from this
  directory to verify the checked-in file remains exact.

## Suggested workflow

1. Load `data/gistemp_annual_global_means.csv` and plot the annual (`J-D`)
   series with a smoothed trend.
2. Compare decadal means (1880s vs 1990s vs 2010s) and quantify the warming
   rate (°C/decade) over the full record and over 1975–present.
3. Save the figure and write a short report — every number from code output.
