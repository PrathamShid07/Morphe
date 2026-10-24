# Python data analysis notes

## NumPy arrays

NumPy ndarray stores homogeneous numbers in contiguous memory. Vectorised operations and broadcasting avoid slow Python loops, and reshape or transpose change the view without copying the data.

## Pandas DataFrames

A pandas DataFrame holds tabular data with labelled columns. groupby aggregates rows by key, merge joins tables like SQL, and pivot_table reshapes values into a summary matrix.

## Missing values

Pandas marks missing values as NaN. Use isna to find them, dropna to remove rows and fillna to substitute a default such as the column median, and check how much data is missing before choosing.

## Plotting

Matplotlib draws figures with axes for line charts, scatter plots and histograms. Seaborn builds statistical plots on top of it, and savefig exports an image at a chosen dpi.

## Reading files

pandas.read_csv loads comma separated files, with dtype and parse_dates controlling types. For files larger than memory read in chunks with the chunksize argument or use parquet for fast columnar storage.

## Time series

A DatetimeIndex enables resample to change frequency, rolling for moving averages and shift to compute differences between periods. Always keep timestamps in UTC internally.
