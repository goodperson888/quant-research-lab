from pandas import DataFrame

from freqtrade.strategy import IStrategy


class EngineSmokeStrategy(IStrategy):
    """Non-trading fixture used only to verify Freqtrade engine wiring."""

    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    minimal_roi = {}
    stoploss = -0.10
    startup_candle_count = 0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe
