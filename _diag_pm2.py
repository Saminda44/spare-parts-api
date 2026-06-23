from src.models.master_data.catalogue_part_master import build_part_master
df = build_part_master()
total = len(df)
empty = (df["description"].str.strip() == "").sum()
print(f"Total: {total}  Missing descriptions: {empty} ({empty/total*100:.1f}%)")

# By source
import pandas as pd
empty_df = df[df["description"].str.strip() == ""].copy()
empty_df["first_src"] = empty_df["source_pdfs"].str.split("; ").str[0]
top = empty_df.groupby("first_src").size().sort_values(ascending=False).head(8)
print("\nBy source:")
for src, cnt in top.items():
    print(f"  {cnt:4d}  {src}")

# Check specific formerly-empty parts
print("\nFormerly-empty R15 parts:")
for pn in ["1S7-E1102-00", "1S7-E1133-10", "90153-06803", "95027-06014"]:
    row = df[df["part_no"] == pn]
    if not row.empty:
        print(f"  {pn}: desc={repr(row.iloc[0]['description'][:50])}")
    else:
        print(f"  {pn}: NOT FOUND")
