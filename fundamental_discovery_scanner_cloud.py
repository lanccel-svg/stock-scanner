"""
펀더멘털 발굴 스캐너  (시총 상위 배치 × DART 재무)
==================================================
목적 : 시총 상위 종목을 매주 30개씩 훑어 '펀더멘털이 좋은/개선되는' 신규 후보 발굴.
       - 3요소·3렌즈 프레임워크, 합불(pass/fail) 기준 없음.
       - 재무지표(성장·마진·ROE·건전성)를 점수화해 배치 안에서 랭킹만 한다.
실행: 클라우드 루틴용(상태파일 없이 주 번호로 배치 자동 진행)
배치 : 1주차 KOSPI 1~30 → 2주차 31~60 → ... → KOSDAQ까지 자동 진행(상태파일).

데이터 소스
  · 시총 순위 : FinanceDataReader(무로그인) 또는 pykrx(KRX 계정 필요)
  · 재무      : DART OpenDartReader  (상장사, 금융업 제외 -> 금융주는 N/A)

전제 : pip install opendartreader finance-datareader pandas  (pykrx는 선택)
DART KEY : https://opendart.fss.or.kr 무료 발급
"""

import os, json, time
import pandas as pd
import OpenDartReader

# ── 설정 ───────────────────────────────────────────────────────
# 클라우드 루틴용: 키/설정은 클라우드 '환경변수'로 주입 (코드/저장소에 키 하드코딩 금지)
DART_API_KEY  = os.getenv("DART_API_KEY")            # 클라우드 환경변수로 주입 (필수)
MARCAP_SOURCE = os.getenv("MARCAP_SOURCE", "fdr")    # "fdr"(무로그인) 또는 "pykrx"(KRX 계정)
LATEST_YEAR   = int(os.getenv("LATEST_YEAR", "2025"))
ANCHOR_DATE   = os.getenv("ANCHOR_DATE", "2026-09-15")  # 1주차(KOSPI 1~30) 기준 월요일 — 첫 실행 주에 맞춰 조정
TOP_N_SHORTLIST = 8            # 배치당 최종 후보 개수

# 주차 계획: (시장, 순위시작, 순위끝)
PLAN = [
    ("KOSPI", 1, 30), ("KOSPI", 31, 60), ("KOSPI", 61, 90), ("KOSPI", 91, 100),
    ("KOSDAQ", 1, 30), ("KOSDAQ", 31, 60), ("KOSDAQ", 61, 90), ("KOSDAQ", 91, 100),
]

# 발굴 점수 가중치(재무만) — 필요시 조정
W_REV_GROWTH = 25   # 매출성장
W_OP_GROWTH  = 25   # 영업이익성장
W_OP_MARGIN  = 20   # 영업이익률
W_ROE        = 20   # 자기자본이익률
W_LOW_DEBT   = 10   # 부채비율 낮으면 가점

# ── 유틸 ───────────────────────────────────────────────────────
def to_num(x):
    if x is None: return None
    s = str(x).strip().replace(",", "")
    if s in ("", "-"): return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try: v = float(s)
    except ValueError: return None
    return -v if neg else v

def clip01(v, lo, hi):
    if v is None: return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))

# ── 시총 순위 유니버스 ─────────────────────────────────────────
def get_universe(market, rank_from, rank_to):
    """(순위, 종목코드, 종목명) 리스트 반환."""
    if MARCAP_SOURCE == "fdr":
        import FinanceDataReader as fdr
        df = fdr.StockListing(market)
        cap = next(c for c in df.columns if c.lower() in ("marcap", "marketcap"))
        code = "Code" if "Code" in df.columns else df.columns[0]
        name = "Name" if "Name" in df.columns else next(c for c in df.columns if "name" in c.lower())
        df = df.dropna(subset=[cap]).sort_values(cap, ascending=False).reset_index(drop=True)
        rows = []
        for i, r in df.iloc[rank_from-1:rank_to].iterrows():
            rows.append((i+1, str(r[code]).zfill(6), r[name]))
        return rows

    elif MARCAP_SOURCE == "pykrx":
        from pykrx import stock
        from datetime import datetime, timedelta
        # KRX_ID / KRX_PW 환경변수 필요(최신 pykrx)
        d = datetime.now()
        for i in range(12):
            ds = (d - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = stock.get_market_cap(ds, market=market)
                if df is not None and len(df) > 50:
                    break
            except Exception:
                df = None
        df = df.sort_values("시가총액", ascending=False)
        rows = []
        for rank, (tkr, _) in enumerate(df.iloc[rank_from-1:rank_to].iterrows(), rank_from):
            rows.append((rank, tkr, stock.get_market_ticker_name(tkr)))
        return rows
    else:
        raise ValueError("MARCAP_SOURCE는 'fdr' 또는 'pykrx'")

# ── DART 재무 3개년 추출 ───────────────────────────────────────
def pick3(df, sj_list, id_cands=(), nm_cands=()):
    """(당기, 전기, 전전기) 금액 튜플."""
    if df is None or len(df) == 0: return (None, None, None)
    sub = df[df["sj_div"].isin(sj_list)]
    def vals(row):
        return (to_num(row.get("thstrm_amount")),
                to_num(row.get("frmtrm_amount")),
                to_num(row.get("bfefrmtrm_amount")))
    for cid in id_cands:
        hit = sub[sub["account_id"] == cid]
        if len(hit): return vals(hit.iloc[0])
    names = sub["account_nm"].astype(str).str.replace(" ", "", regex=False)
    for nm in nm_cands:
        hit = sub[names.str.contains(nm, na=False)]
        if len(hit): return vals(hit.iloc[0])
    return (None, None, None)

def get_financials(dart, code):
    """최신 연간 재무제표 -> 지표 dict. 실패 시 None."""
    df = None
    for yr in (LATEST_YEAR, LATEST_YEAR - 1):
        for fs in ("CFS", "OFS"):
            try:
                d = dart.finstate_all(code, yr, reprt_code="11011", fs_div=fs)
            except Exception:
                d = None
            if d is not None and len(d):
                df = d; break
        if df is not None: break
    if df is None:
        return None

    IS = ["IS", "CIS"]; BS = ["BS"]
    rev = pick3(df, IS, ["ifrs-full_Revenue","dart_OperatingRevenue"], ["매출액","수익(매출액)","영업수익"])
    gp  = pick3(df, IS, ["ifrs-full_GrossProfit"], ["매출총이익"])
    op  = pick3(df, IS, ["dart_OperatingIncomeLoss","ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익"])
    ni  = pick3(df, IS, ["ifrs-full_ProfitLoss"], ["당기순이익","분기순이익"])
    eq  = pick3(df, BS, ["ifrs-full_Equity"], ["자본총계"])
    li  = pick3(df, BS, ["ifrs-full_Liabilities"], ["부채총계"])

    def growth(a):  # 당기/전기 - 1
        return (a[0]/a[1]-1) if a[0] is not None and a[1] not in (None,0) else None
    def ratio(a,b): # a당기/b당기
        return (a[0]/b[0]) if a[0] is not None and b[0] not in (None,0) else None
    def ni_trend(a):
        seq = [v for v in a if v is not None]
        if len(seq) < 2: return None
        return sum(1 for x,y in zip(seq, seq[1:]) if x > y) / (len(seq)-1)  # 상승비율

    return {
        "매출성장":   growth(rev),
        "영업익성장": growth(op),
        "영업이익률": ratio(op, rev),
        "순이익률":   ratio(ni, rev),
        "GPM":       ratio(gp, rev),
        "ROE":       ratio(ni, eq),
        "부채비율":   ratio(li, eq),
        "순익상승추세": ni_trend(ni),
        "매출액":     rev[0], "당기순이익": ni[0],
    }

def score(f):
    if f is None: return None
    s  = W_REV_GROWTH * clip01(f["매출성장"],   0.0, 0.30)
    s += W_OP_GROWTH  * clip01(f["영업익성장"], 0.0, 0.30)
    s += W_OP_MARGIN  * clip01(f["영업이익률"], 0.0, 0.20)
    s += W_ROE        * clip01(f["ROE"],       0.0, 0.20)
    s += W_LOW_DEBT   * (1.0 if (f["부채비율"] is not None and f["부채비율"] < 1.0) else 0.0)
    return round(s, 1)

# ── 배치 실행 ──────────────────────────────────────────────────
def current_batch_index():
    """상태파일 없이 '주 번호'로 배치 결정 — 새 clone이라도 결정적으로 같은 주차."""
    override = os.getenv("BATCH_INDEX")          # 수동 테스트용 강제 지정
    if override is not None:
        return int(override) % len(PLAN)
    from datetime import date
    y, m, d = map(int, ANCHOR_DATE.split("-"))
    weeks = (date.today() - date(y, m, d)).days // 7
    return max(0, weeks) % len(PLAN)

def run():
    if not DART_API_KEY:
        raise SystemExit("DART_API_KEY 환경변수가 없습니다. 클라우드 환경변수에 설정하세요.")
    dart = OpenDartReader(DART_API_KEY)
    idx = current_batch_index()
    market, rf, rt = PLAN[idx]
    print(f"[배치 {idx+1}/{len(PLAN)}] {market} 시총 {rf}~{rt}위\n" + "="*60)

    uni = get_universe(market, rf, rt)
    rows = []
    for rank, code, name in uni:
        f = get_financials(dart, code)
        sc = score(f)
        tag = "" if f else "N/A(금융주/조회실패)"
        rows.append({"순위": rank, "종목": name, "코드": code, "점수": sc,
                     **({k: f[k] for k in ("매출성장","영업익성장","영업이익률","ROE","부채비율","순익상승추세")} if f else {}),
                     "비고": tag})
        print(f"  {rank:3d} {name:<12} {'' if sc is None else f'{sc:5.1f}점'}  {tag}")
        time.sleep(0.1)

    t = pd.DataFrame(rows)
    t_valid = t[t["점수"].notna()].sort_values("점수", ascending=False)

    print("\n" + "="*60 + f"\n■ {market} {rf}~{rt}위 발굴 후보 TOP {TOP_N_SHORTLIST}")
    print("="*60)
    pct = lambda v: "" if v is None or pd.isna(v) else f"{v:+.0%}"
    for _, r in t_valid.head(TOP_N_SHORTLIST).iterrows():
        print(f"{r['점수']:5.1f}점  {r['종목']:<12} "
              f"매출{pct(r.get('매출성장'))} 영익{pct(r.get('영업익성장'))} "
              f"영익률{pct(r.get('영업이익률'))} ROE{pct(r.get('ROE'))}")

    out = f"discovery_{market}_{rf}-{rt}.csv"
    t.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n[저장] {out}")

    nxt = (idx + 1) % len(PLAN)
    print(f"[다음 주차] 배치 {nxt+1}/{len(PLAN)} (다음 주 실행 시 자동)")

if __name__ == "__main__":
    run()
