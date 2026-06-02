#!/usr/bin/env python3
"""SAcare 保險登記 vs EPB 結帳 月對帳工具 (士林門市 004)

用法:
    python3 sacare_reconcile.py --insurance 保險匯出.xls --month 2026-05
    python3 sacare_reconcile.py --insurance 保險匯出.xls --month 2026-05 --out 報告.xlsx

規則重點:
  - 保險端: .xls(實為HTML)。只取「繳費=已繳(Y)」且「保險起日」(=生效/登記日)落在指定月份。
  - 機器序號取「型號 序號 [Apple Pencil序號] [鍵盤序號]」的第一個序號(=主機/iPad)，配件序號忽略。
  - 機況=檢測新機: 序號在 EPB 不一定對得上, 屬正常, 不列為異常(另開分頁按數量核對)。
  - EPB端: 抓該月含 S.A CARE 品項的單據, 同單主機序號配對(去開頭S)。
    主機品類限 iPhone/iPad/Mac/Watch/AirPods, 排除 Apple Pencil(4010)等配件序號。
  - 訂金(TRANS_TYPE=G): 該月不算實際銷售, 若保險已登記則列為異常。
"""
from __future__ import annotations
import argparse, re, subprocess, sys, io
from difflib import SequenceMatcher
import pandas as pd

EPB_QUERY = "/Users/sa/.codex/skills/epbrowser-sales-reporting/scripts/epb_query.py"
C_POLICY, C_DATE, C_CAT, C_MODEL_SN, C_COND, C_PAY = 4, 5, 6, 8, 9, 14
ACTIVITY_CODE = "99901780"   # SA Care 檢測新機活動代碼

# 主機品類 CAT4 (排除 Apple Pencil 4010、鍵盤等配件)
MAIN_CAT4 = {"4004": "iPhone", "4005": "iPad", "4006": "iPad", "4041": "iPad",
             "4001": "Mac", "4002": "Mac", "4038": "Watch", "4014": "AirPods"}


def roc_to_iso(s):
    m = re.match(r"(\d+)/(\d+)/(\d+)", str(s).split()[0]) if pd.notna(s) else None
    return f"{int(m.group(1)) + 1911}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def strip_s(srn: str) -> str:
    return re.sub(r"^S", "", str(srn).upper().strip())


# ---------- 保險端 ----------
def load_insurance(path: str, month: str) -> pd.DataFrame:
    raw = pd.read_html(path)[0].iloc[1:].reset_index(drop=True)
    toks = raw[C_MODEL_SN].map(lambda s: str(s).split())
    df = pd.DataFrame({
        "保單號": raw[C_POLICY], "保險起日": raw[C_DATE].map(roc_to_iso), "產品種類": raw[C_CAT],
        "序號": toks.map(lambda t: t[1] if len(t) > 1 else ""),          # 主機序號
        "配件序號": toks.map(lambda t: " ".join(t[2:]) if len(t) > 2 else ""),
        "機況": raw[C_COND].astype(str).str.replace(r"\s+", "", regex=True),
        "繳費碼": raw[C_PAY].astype(str).str[-1],
    })
    df = df[(df["繳費碼"] == "Y") & (df["保險起日"].str[:7] == month)].copy()
    df["key"] = df["序號"].str.upper().str.strip()
    df["檢測新機"] = df["機況"].str.contains("檢測新機")
    # 全序號集合(含配件), 供 EPB→保險 反查避免誤判
    all_keys = set()
    for _, r in df.iterrows():
        all_keys.update(t.upper() for t in (r["序號"] + " " + r["配件序號"]).split())
    df.attrs["all_keys"] = all_keys
    return df.reset_index(drop=True)


# ---------- EPB 端 ----------
def query_epb(month: str, shop: str) -> pd.DataFrame:
    y, m = int(month[:4]), int(month[5:7])
    start, end = f"{y}-{m:02d}-01", f"{y + (m // 12)}-{(m % 12) + 1:02d}-01"
    sql = f"""
SELECT p.DOC_ID, TO_CHAR(p.DOC_DATE,'YYYY-MM-DD') DOC_DATE, p.LINE_NO, p.TRANS_TYPE,
       p.STK_ID, p.NAME, p.SRN_ID, p.STK_QTY, p.CAT4_ID
FROM POSLINEV_BI p
WHERE p.SHOP_ID='{shop}'
  AND p.DOC_DATE >= TO_DATE('{start}','YYYY-MM-DD') AND p.DOC_DATE < TO_DATE('{end}','YYYY-MM-DD')
  AND p.DOC_ID IN (SELECT DISTINCT q.DOC_ID FROM POSLINEV_BI q
    WHERE q.SHOP_ID='{shop}'
      AND q.DOC_DATE >= TO_DATE('{start}','YYYY-MM-DD') AND q.DOC_DATE < TO_DATE('{end}','YYYY-MM-DD')
      AND q.STK_ID LIKE '999%' AND UPPER(q.NAME) LIKE 'S.A CARE%')
ORDER BY p.DOC_ID, p.LINE_NO
"""
    proc = subprocess.run([sys.executable, EPB_QUERY, "--format", "csv", "--limit", "20000", sql],
                          text=True, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return pd.read_csv(io.StringIO(proc.stdout), dtype=str).fillna("")


def sac_category(name: str) -> str:
    n = str(name).upper()
    if "AIRPODS" in n or "週邊配件" in str(name): return "AirPods"  # 保險端AirPods登記為「週邊配件」
    if "WATCH" in n: return "Watch"
    if "IPHONE" in n: return "iPhone"
    if "IPAD" in n: return "iPad"
    if "MACBOOK" in n or "IMAC" in n or " MAC" in n: return "Mac"
    return "其他"


def build_epb_units(e: pd.DataFrame):
    """展開 SAcare 單位並配對主機序號(含正負號)。
    回傳 (serial_df, noser_df):
      serial_df: 每筆 SAcare→序號 的配對, sign=+1售/-1退, 後續依序號跨單據淨額。
      noser_df : 配不到主機序號的 SAcare 單位(僅正向)。
    """
    e = e.copy()
    e["STK_QTY"] = e["STK_QTY"].astype(float)
    check_docs = set(e.loc[e["STK_ID"] == ACTIVITY_CODE, "DOC_ID"])
    sac = e[e["STK_ID"].str.startswith("999") & e["NAME"].str.upper().str.startswith("S.A CARE")].copy()
    sac["cat"] = sac["NAME"].map(sac_category)
    dev = e[(e["SRN_ID"] != "") & (e["CAT4_ID"].isin(MAIN_CAT4))].copy()   # 只留主機序號
    dev["mcat"] = dev["CAT4_ID"].map(MAIN_CAT4)
    dev["key"] = dev["SRN_ID"].map(strip_s)

    serial_rows, noser_rows = [], []
    for doc in sac["DOC_ID"].unique():
        sg, dg = sac[sac["DOC_ID"] == doc], dev[dev["DOC_ID"] == doc]
        ddate = sg["DOC_DATE"].iloc[0]
        for cat, g in sg.groupby("cat"):
            net = int(round(g["STK_QTY"].sum()))
            if net == 0:
                continue
            sign = 1 if net > 0 else -1
            ttype = "G" if (g["TRANS_TYPE"] == "G").any() else ("A" if net > 0 else "E")
            is_check = bool(g["NAME"].str.contains("檢測新機").any()) or doc in check_docs
            cand = dg[dg["mcat"] == cat]["key"].tolist()
            for i in range(abs(net)):
                rec = {"DOC_ID": doc, "日期": ddate, "SAcare品項": g["NAME"].iloc[0], "品類": cat,
                       "交易別": ttype, "檢測新機": is_check, "sign": sign}
                key = cand[i] if i < len(cand) else ""
                if key:
                    serial_rows.append({**rec, "EPB序號去S": key})
                elif sign > 0:
                    noser_rows.append(rec)
    return pd.DataFrame(serial_rows), pd.DataFrame(noser_rows)


# ---------- 比對 ----------
def reconcile(ins: pd.DataFrame, serial_df: pd.DataFrame, noser_df: pd.DataFrame, fuzzy_thr=0.7):
    ins_all_keys = ins.attrs["all_keys"]
    ins_new = ins[~ins["檢測新機"]].copy()
    ins_check = ins[ins["檢測新機"]].copy()

    # 依主機序號跨單據淨額 (賣+1/退-1)
    if len(serial_df):
        net = serial_df.groupby("EPB序號去S")["sign"].sum()
        eff_keys = set(net[net > 0].index)            # 淨額>0 = 實際有 SAcare 的序號
        pos = serial_df[serial_df["sign"] > 0]
        info = pos.sort_values("檢測新機").groupby("EPB序號去S").agg(
            DOC_ID=("DOC_ID", "last"), 品類=("品類", "last"), SAcare品項=("SAcare品項", "last"),
            是否訂金=("交易別", lambda s: (s == "G").any()),
            檢測新機=("檢測新機", "any")).reset_index()
        info = info[info["EPB序號去S"].isin(eff_keys)].copy()
    else:
        info = pd.DataFrame(columns=["EPB序號去S", "DOC_ID", "品類", "SAcare品項", "是否訂金", "檢測新機"])
        eff_keys = set()

    normal_eff = info[(~info["是否訂金"]) & (~info["檢測新機"])]
    deposit_eff = info[info["是否訂金"]].copy()
    check_eff = info[info["檢測新機"] & (~info["是否訂金"])]
    ek_normal = set(normal_eff["EPB序號去S"])
    dep_reg_keys = set(deposit_eff["EPB序號去S"]) & ins_all_keys

    matched = ins_new[ins_new["key"].isin(ek_normal)].copy()
    # 差異2 = 保險(新機)有, 但 EPB 無有效 SAcare (含: 從未結 / 已退淨額0)
    only_ins = ins_new[(~ins_new["key"].isin(ek_normal)) & (~ins_new["key"].isin(dep_reg_keys))].copy()
    only_epb = normal_eff[~normal_eff["EPB序號去S"].isin(ins_all_keys)].copy()

    # 模糊配對(同機序號登打差異)
    typo_rows, oi_idx, oe_idx = [], set(), set()
    for i, a in only_ins.iterrows():
        bj, br = None, 0.0
        for j, b in only_epb.iterrows():
            if j in oe_idx:
                continue
            r = SequenceMatcher(None, a["key"], b["EPB序號去S"]).ratio()
            if r > br:
                br, bj = r, j
        if bj is not None and br >= fuzzy_thr:
            b = only_epb.loc[bj]
            typo_rows.append({"保單號": a["保單號"], "保險起日": a["保險起日"], "產品種類": a["產品種類"],
                              "保險序號": a["序號"], "EPB序號去S": b["EPB序號去S"],
                              "EPB單據": b["DOC_ID"], "相似度": round(br, 2)})
            oi_idx.add(i); oe_idx.add(bj)
    typo = pd.DataFrame(typo_rows)
    only_ins, only_epb = only_ins.drop(index=oi_idx), only_epb.drop(index=oe_idx)

    # 訂金: 標記保險是否已登記
    deposit_eff["保險已登記"] = deposit_eff["EPB序號去S"].apply(lambda k: "是" if k in ins_all_keys else "否")

    # EPB 多打/無主機序號 (正向且非檢測非訂金)
    excess = noser_df[(~noser_df.get("檢測新機", False)) & (noser_df.get("交易別", "") != "G")].copy() \
        if len(noser_df) else pd.DataFrame(columns=["DOC_ID", "品類", "SAcare品項"])

    # 檢測新機(正常) 數量核對 (品類對齊)
    ic = ins_check["產品種類"].map(sac_category).value_counts().rename("保險筆數")
    epb_check_units = pd.concat([check_eff[["品類"]],
                                 noser_df[noser_df.get("檢測新機", False)][["品類"]] if len(noser_df) else pd.DataFrame(columns=["品類"])])
    ec = epb_check_units["品類"].value_counts().rename("EPB筆數")
    check_tbl = pd.concat([ic, ec], axis=1).fillna(0).astype(int)
    check_tbl["相符"] = check_tbl["保險筆數"] == check_tbl["EPB筆數"]
    check_tbl = check_tbl.reset_index(names="品類")

    epb_check_detail = pd.concat([
        check_eff[["DOC_ID", "品類", "SAcare品項", "EPB序號去S"]],
        (noser_df[noser_df.get("檢測新機", False)].assign(**{"EPB序號去S": "(無序號)"})[
            ["DOC_ID", "品類", "SAcare品項", "EPB序號去S"]] if len(noser_df) else pd.DataFrame())
    ], ignore_index=True)

    # 合併所有 SAcare 單位(含正負號/日期), 供品類與逐日彙總
    cols = ["日期", "品類", "sign"]
    all_units = pd.concat([df[cols] for df in (serial_df, noser_df) if len(df)], ignore_index=True) \
        if (len(serial_df) or len(noser_df)) else pd.DataFrame(columns=cols)

    # ① 品類數量比較 (保險 vs EPB淨額) — 粗估哪個品項有差
    ic = ins["產品種類"].map(sac_category).value_counts().rename("保險筆數")
    ec = all_units.groupby("品類")["sign"].sum().rename("EPB淨額") if len(all_units) else pd.Series(dtype=int, name="EPB淨額")
    cat_tbl = pd.concat([ic, ec], axis=1).fillna(0).astype(int)
    cat_tbl["差異"] = cat_tbl["保險筆數"] - cat_tbl["EPB淨額"]
    cat_tbl = cat_tbl.reset_index(names="品類").sort_values("品類")

    # ② 逐日總量比較 (保險起日 vs EPB SAcare淨額)
    idl = ins["保險起日"].value_counts().rename("保險筆數")
    edl = all_units.groupby("日期")["sign"].sum().rename("EPB淨額") if len(all_units) else pd.Series(dtype=int, name="EPB淨額")
    daily_tbl = pd.concat([idl, edl], axis=1).fillna(0).astype(int)
    daily_tbl["差異"] = daily_tbl["保險筆數"] - daily_tbl["EPB淨額"]
    daily_tbl = daily_tbl.reset_index(names="日期").sort_values("日期")

    return dict(matched=matched, typo=typo, only_ins=only_ins, only_epb=only_epb,
                excess=excess, deposit=deposit_eff, ins_check=ins_check,
                epb_check_detail=epb_check_detail, check_tbl=check_tbl,
                cat_tbl=cat_tbl, daily_tbl=daily_tbl,
                n_epb_units=len(serial_df[serial_df["sign"] > 0]) + len(noser_df) if len(serial_df) else len(noser_df))


def run(insurance_path: str, month: str, shop: str = "004") -> dict:
    """供 server / 程式呼叫: 回傳 (ins, reconcile結果dict)。"""
    ins = load_insurance(insurance_path, month)
    serial_df, noser_df = build_epb_units(query_epb(month, shop))
    return ins, reconcile(ins, serial_df, noser_df)


def detect_month(insurance_path: str) -> str:
    raw = pd.read_html(insurance_path)[0].iloc[1:]
    months = raw[C_DATE].map(roc_to_iso).dropna().str[:7]
    return months.mode().iloc[0] if len(months) else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insurance", required=True)
    ap.add_argument("--month", required=True, help="YYYY-MM (依保險起日)")
    ap.add_argument("--shop", default="004")
    ap.add_argument("--out")
    args = ap.parse_args()
    out = args.out or f"SAcare對帳_{args.month}_{args.shop}.xlsx"

    ins = load_insurance(args.insurance, args.month)
    serial_df, noser_df = build_epb_units(query_epb(args.month, args.shop))
    R = reconcile(ins, serial_df, noser_df)

    summary = pd.DataFrame({"項目": [
        "比對月份", "門市", "保險已繳筆數", "其中-新機", "其中-檢測新機",
        "EPB SAcare單位(正向)",
        "✅ 完全相符(新機)", "⚠️ 序號登記差異(同機)", "❌ 保險有/EPB無有效SAcare(新機)",
        "❌ EPB有/保險未登記(新機)", "⚠️ EPB一台多打/無主機序號",
        "⚠️ 訂金(其中保險已登記)", "ℹ️ 檢測新機(正常,另頁核對)"],
        "數值": [args.month, args.shop, len(ins), int((~ins["檢測新機"]).sum()), int(ins["檢測新機"].sum()),
                 R["n_epb_units"], len(R["matched"]), len(R["typo"]), len(R["only_ins"]),
                 len(R["only_epb"]), len(R["excess"]),
                 f"{len(R['deposit'])} (已登記 {(R['deposit']['保險已登記']=='是').sum()})",
                 len(R["epb_check_detail"])]})

    with pd.ExcelWriter(out, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="摘要", index=False)
        R["cat_tbl"].to_excel(w, sheet_name="品類數量比較", index=False)
        R["daily_tbl"].to_excel(w, sheet_name="逐日總量比較", index=False)
        R["matched"][["保單號", "保險起日", "產品種類", "序號"]].to_excel(w, sheet_name="相符", index=False)
        _safe(R["typo"]).to_excel(w, sheet_name="差異1_序號登記差異", index=False)
        R["only_ins"][["保單號", "保險起日", "產品種類", "序號"]].to_excel(w, sheet_name="差異2_保險有EPB無", index=False)
        R["only_epb"][["DOC_ID", "品類", "SAcare品項", "EPB序號去S"]].to_excel(w, sheet_name="差異3_EPB有保險無", index=False)
        _safe(R["excess"][["DOC_ID", "品類", "SAcare品項"]] if len(R["excess"]) else R["excess"]).to_excel(w, sheet_name="差異4_EPB多打無序號", index=False)
        _safe(R["deposit"][["DOC_ID", "品類", "SAcare品項", "EPB序號去S", "保險已登記"]] if len(R["deposit"]) else R["deposit"]).to_excel(w, sheet_name="差異5_訂金", index=False)
        R["check_tbl"].to_excel(w, sheet_name="檢測新機_數量核對", index=False)
        R["ins_check"][["保單號", "保險起日", "產品種類", "序號"]].to_excel(w, sheet_name="檢測新機_保險明細", index=False)
        _safe(R["epb_check_detail"]).to_excel(w, sheet_name="檢測新機_EPB明細", index=False)

    print(f"已輸出: {out}")
    for _, r in summary.iterrows():
        print(f"  {r['項目']}: {r['數值']}")
    return 0


def _safe(df):
    return df if len(df) else pd.DataFrame(columns=["(無)"])


if __name__ == "__main__":
    raise SystemExit(main())
