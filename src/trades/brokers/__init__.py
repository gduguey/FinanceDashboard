"""One module per broker. Each fetches that broker's own trade/position/cash
data and writes it to `data/brokers/{broker}/`, in whatever shape that
broker's API naturally returns — normalizing across brokers into a single
schema is a problem for whoever consumes them, not for these modules.
"""
