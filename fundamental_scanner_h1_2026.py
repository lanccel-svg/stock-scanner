"""
펀더멘털 발굴 스캐너 - 2026 상반기 기준 (반기 YoY 비교)
PBR = 자본총계 / (보통주자본금 / 액면가) 로 계산 (DART 기준, 주가 불필요)
     → 실제 주가 기반 PBR은 시장가격 API 차단으로 산출 불가
"""
import os, time, requests
import pandas as pd
import OpenDartReader

DART_API_KEY  = os.getenv("DART_API_KEY")
LATEST_YEAR   = 2026
LATEST_REPRT  = "11012"   # 반기보고서 (2026 H1)
PREV_REPRT    = "11011"   # 사업보고서 (2025 연간, fallback)
TOP_N         = 8

# 액면가 매핑 (자본금 / 액면가 = 보통주 발행주식수)
# 출처: 각사 정관 / 등기부
PAR_VALUE = {
    "005930": 100,   "000660": 5000,  "373220": 500,   "207940": 500,
    "005380": 5000,  "005490": 5000,  "000270": 5000,  "035420": 100,
    "068270": 1000,  "105560": 5000,  "028260": 100,   "051910": 5000,
    "055550": 5000,  "086790": 5000,  "017670": 500,   "035720": 100,
    "012450": 5000,  "006400": 5000,  "034020": 5000,  "033780": 5000,
    "012330": 5000,  "267250": 500,   "096770": 5000,  "003670": 500,
    "066570": 5000,  "003550": 5000,  "034730": 500,   "032830": 500,
    "030200": 5000,  "011070": 5000,
    # KOSDAQ
    "247540": 500,   "086520": 500,   "028300": 100,   "196170": 500,
    "141080": 100,   "214150": 100,   "277810": 100,   "214450": 100,
    "058470": 500,   "036930": 500,   "357780": 500,   "145020": 500,
    "145720": 500,   "066970": 500,   "293490": 100,   "096530": 1000,
    "068760": 1000,  "035900": 500,   "041510": 500,   "263750": 500,
    "031980": 500,   "240810": 500,   "022100": 100,   "237690": 500,
    "078340": 500,   "086900": 500,   "348370": 500,   "035760": 500,
    "067310": 500,   "263720": 100,
}

def to_num(x):
    if x is None: return None
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "None"): return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try: return -float(s) if neg else float(s)
    except: return None

def clip01(v, lo, hi):
    if v is None: return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))

def pick_val(df, sj_list, id_cands, nm_cands, col="thstrm_amount"):
    if df is None or len(df) == 0: return None
    sub = df[df["sj_div"].isin(sj_list)]
    # 반기보고서: frmtrm_amount가 NaN이고 frmtrm_q_amount에 전년동기가 있음
    actual_col = col
    if col == "frmtrm_amount" and "frmtrm_q_amount" in df.columns:
        # frmtrm_q_amount = 전년동기(H1 YoY 비교용)
        actual_col = "frmtrm_q_amount"
    for cid in id_cands:
        hit = sub[sub.get("account_id", pd.Series(dtype=str)) == cid]
        if len(hit):
            v = to_num(hit.iloc[0].get(actual_col))
            # fallback: try original col if actual_col is None
            if v is None and actual_col != col:
                v = to_num(hit.iloc[0].get(col))
            return v
    names = sub["account_nm"].astype(str).str.replace(" ", "", regex=False)
    for nm in nm_cands:
        hit = sub[names == nm]
        if len(hit):
            v = to_num(hit.iloc[0].get(actual_col))
            if v is None and actual_col != col:
                v = to_num(hit.iloc[0].get(col))
            return v
    return None

def get_financials(dart, code):
    """2026 반기보고서 우선, 없으면 2025 연간. 반기는 YoY(전년동기) 비교."""
    df, period = None, None
    # 1) 2026 반기
    for fs in ("CFS", "OFS"):
        try:
            d = dart.finstate_all(code, 2026, reprt_code=LATEST_REPRT, fs_div=fs)
        except: d = None
        if d is not None and len(d):
            df, period = d, "2026H1"
            break
    # 2) fallback: 2025 연간
    if df is None:
        for yr in (2025, 2024):
            for fs in ("CFS", "OFS"):
                try:
                    d = dart.finstate_all(code, yr, reprt_code=PREV_REPRT, fs_div=fs)
                except: d = None
                if d is not None and len(d):
                    df, period = d, f"{yr}FY"
                    break
            if df is not None: break
    if df is None: return None

    IS = ["IS", "CIS"]; BS = ["BS"]
    # 당기(thstrm) = 2026H1 or latest, 전기(frmtrm) = YoY 비교 기준
    rev_c  = pick_val(df, IS, ["ifrs-full_Revenue","dart_OperatingRevenue"], ["매출액","수익(매출액)","영업수익"])
    rev_p  = pick_val(df, IS, ["ifrs-full_Revenue","dart_OperatingRevenue"], ["매출액","수익(매출액)","영업수익"], "frmtrm_amount")
    op_c   = pick_val(df, IS, ["dart_OperatingIncomeLoss","ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익"])
    op_p   = pick_val(df, IS, ["dart_OperatingIncomeLoss","ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익"], "frmtrm_amount")
    ni_c   = pick_val(df, IS, ["ifrs-full_ProfitLoss"], ["당기순이익","분기순이익"])
    ni_p   = pick_val(df, IS, ["ifrs-full_ProfitLoss"], ["당기순이익","분기순이익"], "frmtrm_amount")
    gp_c   = pick_val(df, IS, ["ifrs-full_GrossProfit"], ["매출총이익"])
    eq_c   = pick_val(df, BS, ["ifrs-full_Equity"], ["자본총계"])
    li_c   = pick_val(df, BS, ["ifrs-full_Liabilities"], ["부채총계"])

    # 보통주 자본금 (BPS 계산용)
    cap_com = pick_val(df, BS, ["dart_IssuedCapitalOfCommonStock"], ["보통주자본금"])
    if cap_com is None:
        cap_com = pick_val(df, BS, ["ifrs-full_IssuedCapital"], ["자본금"])

    def growth(c, p):
        return (c / p - 1) if c is not None and p not in (None, 0) else None
    def ratio(a, b):
        return (a / b) if a is not None and b not in (None, 0) else None

    # BPS = 자본총계 / 보통주 발행주식수
    par = PAR_VALUE.get(code)
    bps = None
    if eq_c and cap_com and par:
        shares = cap_com / par
        if shares > 0:
            bps = eq_c / shares

    return {
        "period":    period,
        "매출성장":   growth(rev_c, rev_p),
        "영업익성장": growth(op_c, op_p),
        "영업이익률": ratio(op_c, rev_c),
        "순이익률":   ratio(ni_c, rev_c),
        "GPM":       ratio(gp_c, rev_c),
        "ROE":       ratio(ni_c, eq_c),   # 반기 net income / 자본총계 (단순비율)
        "부채비율":   ratio(li_c, eq_c),
        "BPS(원)":   round(bps) if bps else None,
        "매출액":     rev_c, "당기순이익": ni_c,
    }

def score(f):
    if f is None: return None
    s  = 25 * clip01(f["매출성장"],   0.0, 0.30)
    s += 25 * clip01(f["영업익성장"], 0.0, 0.30)
    s += 20 * clip01(f["영업이익률"], 0.0, 0.20)
    s += 20 * clip01(f["ROE"],       0.0, 0.20)
    s += 10 * (1.0 if f["부채비율"] is not None and f["부채비율"] < 1.0 else 0.0)
    return round(s, 1)

def scan(dart, universe, label):
    print(f"\n{'='*64}\n■ {label}\n{'='*64}")
    rows = []
    for rank, code, name in universe:
        f = get_financials(dart, code)
        sc = score(f)
        period = f["period"] if f else "-"
        tag = "" if f else "N/A"
        rows.append({
            "순위": rank, "종목": name, "코드": code,
            "기준": period, "점수": sc,
            **({k: f[k] for k in ("매출성장","영업익성장","영업이익률","ROE","부채비율","BPS(원)")} if f else {}),
            "비고": tag,
        })
        bps_str = f"BPS {f['BPS(원)']:,.0f}원" if f and f.get("BPS(원)") else ""
        print(f"  {rank:3d} {name:<16} {'' if sc is None else f'{sc:5.1f}점'}  [{period}]  {bps_str}  {tag}")
        time.sleep(0.08)

    t = pd.DataFrame(rows)
    return t

KOSPI30 = [
    (1,"005930","삼성전자"),(2,"000660","SK하이닉스"),(3,"373220","LG에너지솔루션"),
    (4,"207940","삼성바이오로직스"),(5,"005380","현대자동차"),(6,"005490","POSCO홀딩스"),
    (7,"000270","기아"),(8,"035420","NAVER"),(9,"068270","셀트리온"),(10,"105560","KB금융"),
    (11,"028260","삼성물산"),(12,"051910","LG화학"),(13,"055550","신한지주"),
    (14,"086790","하나금융지주"),(15,"017670","SK텔레콤"),(16,"035720","카카오"),
    (17,"012450","한화에어로스페이스"),(18,"006400","삼성SDI"),(19,"034020","두산에너빌리티"),
    (20,"033780","KT&G"),(21,"012330","현대모비스"),(22,"267250","HD현대"),
    (23,"096770","SK이노베이션"),(24,"003670","포스코퓨처엠"),(25,"066570","LG전자"),
    (26,"003550","LG"),(27,"034730","SK"),(28,"032830","삼성생명"),
    (29,"030200","KT"),(30,"011070","LG이노텍"),
]

KOSDAQ30 = [
    (1,"247540","에코프로비엠"),(2,"086520","에코프로"),(3,"028300","HLB"),
    (4,"196170","알테오젠"),(5,"141080","리가켐바이오"),(6,"214150","클래시스"),
    (7,"277810","레인보우로보틱스"),(8,"214450","파마리서치"),(9,"058470","리노공업"),
    (10,"036930","주성엔지니어링"),(11,"357780","솔브레인"),(12,"145020","휴젤"),
    (13,"145720","덴티움"),(14,"066970","엘앤에프"),(15,"293490","카카오게임즈"),
    (16,"096530","씨젠"),(17,"068760","셀트리온제약"),(18,"035900","JYP Ent."),
    (19,"041510","에스엠"),(20,"263750","펄어비스"),(21,"031980","피에스케이홀딩스"),
    (22,"240810","원익IPS"),(23,"022100","포스코DX"),(24,"237690","에스티팜"),
    (25,"078340","컴투스"),(26,"086900","메디톡스"),(27,"348370","엔켐"),
    (28,"035760","CJ ENM"),(29,"067310","하나마이크론"),(30,"263720","디앤씨미디어"),
]

def fmt(v, pct=False, x=False):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "-"
    if pct: return f"{v*100:+.0f}%"
    if x:   return f"{v:.1f}x"
    return str(v)

if __name__ == "__main__":
    if not DART_API_KEY:
        raise SystemExit("DART_API_KEY 환경변수 없음")
    dart = OpenDartReader(DART_API_KEY)

    kospi_df  = scan(dart, KOSPI30,  "KOSPI 시총 1~30위 (2026H1 반기 YoY)")
    kosdaq_df = scan(dart, KOSDAQ30, "KOSDAQ 시총 1~30위 (2026H1 반기 YoY)")

    for df, name in [(kospi_df, "KOSPI"), (kosdaq_df, "KOSDAQ")]:
        out = f"discovery_{name}_1-30_2026H1.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[저장] {out}")

    print("\n완료")
