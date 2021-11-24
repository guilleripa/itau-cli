# %%
import numpy as np
import pandas as pd

VIEW_COLS = [
    "description",
    "beneficiary",
    "additional_description",
    "type",
    "op_value",
    "date",
]

MONTHS = [
    "Ene",
    "Feb",
    "Mar",
    "Abr",
    "May",
    "Jun",
    "Jul",
    "Ago",
    "Set",
    "Oct",
    "Nov",
    "Dic",
]

FIRST_MONTH = "2021-02"

historic_dolar = (
    pd.read_excel("results/historic_dolar.xlsx", header=5)
    .iloc[2:-3, :5]  # Erase metadata rows and unused currencies
    .replace(" ", np.nan)
    .ffill()
)
historic_dolar["Mes"] = historic_dolar["Mes"].str[:3].replace("Sep", "Set")
historic_dolar["date"] = pd.to_datetime(
    historic_dolar["Día"].astype(str)
    + " "
    + historic_dolar["Mes"].map(
        {month: str(idx + 1) for idx, month in enumerate(MONTHS)}
    )
    + " "
    + historic_dolar["Año"].astype(int).astype(str),
    infer_datetime_format=True,
)

historic_dolar["Compra"] = historic_dolar["Compra"].astype(float)
historic_dolar["Venta"] = historic_dolar["Venta"].astype(float)
historic_dolar["usd_inter"] = (historic_dolar["Compra"] + historic_dolar["Venta"]) / 2
# %%
pesos = pd.read_csv("results/1401456-UYU.csv", header=0, sep="\t", parse_dates=["date"])
pesos = pd.merge(pesos, historic_dolar[["date", "usd_inter"]], how="left", on="date")
dol = pd.read_csv("results/1401464-USD.csv", header=0, sep="\t", parse_dates=["date"])
dol = pd.merge(dol, historic_dolar[["date", "usd_inter"]], how="left", on="date")
dol["usd_balance"] = dol["balance"]
dol["usd_credit"] = dol["credit"]
dol["usd_debit"] = dol["debit"]
dol["balance"] = dol["balance"] * dol["usd_inter"]
dol["credit"] = dol["credit"] * dol["usd_inter"]
dol["debit"] = dol["debit"] * dol["usd_inter"]


pesos["year_month"] = pesos["date"].dt.to_period("M")
pesos = pesos[pesos["year_month"] >= FIRST_MONTH]
dol["year_month"] = dol["date"].dt.to_period("M")
dol = dol[dol["year_month"] >= FIRST_MONTH]
montly_usd_mean = historic_dolar.groupby(historic_dolar["date"].dt.to_period("M"))[
    "usd_inter"
].mean()

# %%
unidos = pd.concat([pesos, dol]).sort_values(by="date").reset_index()
# %% Esta es la transferencia de mamá.
# Acá no tiene sentido porque se balancea con gastos del año pasado
unidos = unidos[unidos["description"] != "DEP. BUZON EFE012800425"]

# %%
idx = pd.date_range(unidos["date"].min(), unidos["date"].max())
pesos_range = pd.Series(0, index=idx)
dolar_range = pd.Series(0, index=idx)
pesos_balance = pesos.groupby("date")["balance"].last()
dolar_balance = dol.groupby("date")["balance"].last()
pesos_range.loc[pesos_balance.index] += pesos_balance
pesos_range[pesos_range == 0] = np.nan
pesos_range = pesos_range.ffill().bfill()
dolar_range.loc[dolar_balance.index] += dolar_balance
dolar_range[dolar_range == 0] = np.nan
dolar_range = dolar_range.ffill().bfill()
# %%
united_balance = pesos_range + dolar_range
united_balance.plot()
# %%
monthly_savings = unidos.groupby("year_month")["op_value"].sum()
monthly_savings.plot()
mean_monthly_saving = monthly_savings.mean()


# %%
income_desc_keywords = [
    "D.G.I",
    "B.P.S",
]
income_detail_desc_keywords = ["Pento", "IVA Diciembre"]
income_honoraries = ["Eliana Bertolotti"]
income_df = unidos[
    unidos["description"].str.contains("|".join(income_desc_keywords))
    | unidos["additional_description"].str.contains(
        "|".join(income_detail_desc_keywords)
    )
    | unidos["beneficiary"].str.contains("|".join(income_honoraries))
]

spending_df = unidos[
    ~unidos["description"].str.contains("|".join(income_desc_keywords)).fillna(False)
    & ~unidos["additional_description"]
    .str.contains("|".join(income_detail_desc_keywords))
    .fillna(False)
    & ~unidos["beneficiary"].str.contains("|".join(income_honoraries)).fillna(False)
]
# %%
monthly_income = (
    income_df.groupby("year_month")["op_value"].sum()
    / dol.groupby("year_month")["usd_inter"].mean()
)
monthly_income = monthly_income.rolling(2).sum() / 2
monthly_income.plot(), monthly_income
# %%
monthly_spending = -spending_df.groupby("year_month")["op_value"].sum()
monthly_spending = monthly_spending / montly_usd_mean[monthly_spending.index]
# %%
monthly_savings = monthly_income - monthly_spending
monthly_savings.plot(), monthly_savings
# %%
spending_df[spending_df["year_month"] == "2021-03"][VIEW_COLS]
# %%
spending_df[spending_df["year_month"] == "2021-09"][VIEW_COLS].sort_values(
    by="op_value"
).iloc[0:50]
# %%

# %%
