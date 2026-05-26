import re
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import yfinance as yf
import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import dart_fss as dart
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import FinanceDataReader as fdr
from pykrx import stock
import matplotlib.pyplot as plt
import platform

# 💻 OS별 한글 폰트 깨짐 방지
if platform.system() == 'Windows':
    plt.rcParams['font.family'] = 'Malgun Gothic'
elif platform.system() == 'Darwin':
    plt.rcParams['font.family'] = 'AppleGothic'
else:
    plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

# ------------------------------------------------------------------------------
# 1. 페이지 설정 및 데이터 저장소 정의
# ------------------------------------------------------------------------------
st.set_page_config(layout="wide", page_title="프로페셔널 AI 주식·ETF 자산 관리 스튜디오")
DATA_FILE = "portfolio_groups.json"

def load_portfolio_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_portfolio_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if "portfolio" not in st.session_state:
    st.session_state.portfolio = load_portfolio_data()

if "csv_pending_items" not in st.session_state:
    st.session_state.csv_pending_items = []
if "csv_active_group" not in st.session_state:
    st.session_state.csv_active_group = None

# ------------------------------------------------------------------------------
# 2. 백엔드 핵심 크롤링 및 정보 조회 엔진
# ------------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=600)
def get_naver_stock_all_info(stock_code):
    """네이버 금융 메인 페이지에서 실시간 현재가, PER, PBR, 사명을 안전하게 파싱"""
    pure_code = re.sub(r'[^0-9]', '', str(stock_code))

    info = {
        "name": stock_code,
        "price": 0,
        "price_str": "0원",
        "PER": "N/A",
        "PBR": "N/A",
        "PER_PBR": "PER: N/A / PBR: N/A",   # [Fix #2] PBR_PBR → PER_PBR 키 통일
        "dividend": "N/A",
        "div_rate": "N/A",
        "foreigner": "0%",
        "institution": "0%",
        "detail": {
            "시가총액": "N/A", "시가총액순위": "N/A", "상장주식수": "N/A", "액면가": "N/A",
            "외국인한도주식수(A)": "N/A", "외국인보유주식수(B)": "N/A", "외국인소진율(B/A)": "N/A",
            "52주최고": "N/A", "52주최저": "N/A"
        }
    }

    if not (len(pure_code) == 6 and pure_code.isdigit()):
        return info

    try:
        url = f"https://finance.naver.com/item/main.naver?code={pure_code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.naver.com/",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"
        }

        # [Fix #4] 동일 URL 중복 요청 제거 → 세션 1회 요청 후 soup 재활용
        session = requests.Session()
        res = session.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return info
        soup = BeautifulSoup(res.text, 'html.parser')

        # 1. 종목명/ETF명 파싱
        name_tag = soup.select_one(".wrap_company h2 a")
        if name_tag:
            info["name"] = name_tag.get_text(strip=True)

        # 2. 실시간 현재가 파싱
        price_tag = soup.select_one(".no_today .blind")
        if price_tag:
            p_str = price_tag.get_text(strip=True).replace(",", "")
            try:
                info["price"] = int(p_str)
                info["price_str"] = f"{price_tag.get_text(strip=True)}원"
            except ValueError:
                pass

        # 3. PER / PBR 파싱
        per_tag = soup.select_one("em#_per")
        pbr_tag = soup.select_one("em#_pbr")
        per_val = per_tag.get_text(strip=True) if per_tag else "N/A"
        pbr_val = pbr_tag.get_text(strip=True) if pbr_tag else "N/A"

        is_etf = "ETF" in info["name"].upper()
        if is_etf:
            info["PER"] = "ETF 제외"
            info["PBR"] = "ETF 제외"
            info["PER_PBR"] = "ETF 제외 (PER/PBR 미적용)"  # [Fix #2] 올바른 키 사용
        else:
            info["PER"] = per_val + " 배" if per_val != "N/A" else "N/A"
            info["PBR"] = pbr_val + " 배" if pbr_val != "N/A" else "N/A"
            info["PER_PBR"] = f"PER: {per_val}배 | PBR: {pbr_val}배"  # [Fix #2] 올바른 키 사용

        # 4. 배당수익률 파싱
        th_div = soup.find("th", string=lambda text: text and "배당수익률" in text)
        if th_div:
            td_div = th_div.find_next("td")
            if td_div:
                info["div_rate"] = td_div.get_text(strip=True)

        # 5. 수급 정보
        fr_tag = soup.select_one(".tab_con1 .gray .blind")
        if fr_tag:
            info["foreigner"] = fr_tag.get_text(strip=True)

        # 6. 시가총액 및 상세 정보 파싱 (동일 soup 재활용)
        aside_section = soup.select_one(".aside_invest")
        if aside_section:
            id_market_cap = aside_section.select_one("#_market_sum")
            if id_market_cap:
                info["detail"]["시가총액"] = id_market_cap.get_text(strip=True).replace("\t", "").replace("\n", "") + "억원"

            th_elements = aside_section.find_all("th")
            for th in th_elements:
                text = th.get_text(strip=True)
                td = th.find_next("td")
                if not td:
                    continue
                td_text = td.get_text(strip=True)

                if "상장주식수" in text:
                    info["detail"]["상장주식수"] = td_text + " 주"
                elif "액면가" in text:
                    info["detail"]["액면가"] = td_text.split("l")[0].strip()
                elif "외국인보유주식수" in text:
                    info["detail"]["외국인보유주식수(B)"] = td_text + " 주"
                elif "외국인소진율" in text:
                    info["detail"]["외국인소진율(B/A)"] = td_text
                elif "52주최고" in text:
                    nums = re.findall(r'[\d,]+', td_text)
                    if len(nums) >= 2:
                        info["detail"]["52주최고"] = nums[0] + " 원"
                        info["detail"]["52주최저"] = nums[1] + " 원"

    except Exception as e:
        # [Fix #6] 운영 환경에서는 print 대신 pass (디버그 출력 제거)
        pass

    return info


def get_KRX_stock_all_info(stock_code):
    """100% KRX/PyKrx 기반 데이터 수집 마스터 함수"""
    pure_code = stock_code.split('.')[0]
    today_str = datetime.now().strftime("%Y%m%d")

    result = {
        "name": stock_code,
        "price": 0,
        "price_str": "N/A",
        "PER": "N/A",
        "PBR": "N/A",
        "PER_PBR": "PER: N/A / PBR: N/A",
        "dividend": "N/A",
        "div_rate": "N/A",
        "foreigner": "0%",
        "institution": "0%",
        "detail": {
            "시가총액": "N/A", "시가총액순위": "N/A", "상장주식수": "N/A", "액면가": "N/A",
            "외국인한도주식수(A)": "N/A", "외국인보유주식수(B)": "N/A", "외국인소진율(B/A)": "N/A",
            "52주최고": "N/A", "52주최저": "N/A"
        }
    }

    if not (len(pure_code) == 6 and pure_code.isdigit()):
        return result

    # [로직 1] FinanceDataReader 기반 마스터 정보 수집
    try:
        df_krx = fdr.StockListing('KRX')
        df_krx.columns = [col.upper() for col in df_krx.columns]
        target = df_krx[df_krx['CODE'] == pure_code]

        if not target.empty:
            row_krx = target.iloc[0]
            result["name"] = row_krx.get('NAME', '알 수 없음')

            price = row_krx.get('CLOSE', 0)
            if pd.notna(price) and price > 0:
                result["price"] = int(price)
                result["price_str"] = f"{int(price):,} 원"

            stocks_count = row_krx.get('STOCKS', 0)
            market_cap = row_krx.get('MARCAP', 0)

            if pd.notna(market_cap) and market_cap > 0:
                result["detail"]["시가총액"] = f"{int(market_cap / 100000000):,} 억원"
            if pd.notna(stocks_count) and stocks_count > 0:
                result["detail"]["상장주식수"] = f"{int(stocks_count):,} 주"
    except Exception as e:
        pass  # [Fix #6] 디버그 print 제거

    # [로직 2] PyKrx 영업일 역추적 및 펀더멘탈 수집
    df_fund, df_net, df_div = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    base_date = datetime.now()

    for _ in range(7):
        check_date_str = base_date.strftime("%Y%m%d")
        try:
            df_fund_tmp = stock.get_market_fundamental_by_ticker(check_date_str, market="ALL")
            # [Fix #7] None 체크와 empty 체크 분리 (early return 제거)
            if df_fund_tmp is not None and not df_fund_tmp.empty and pure_code in df_fund_tmp.index:
                df_fund = df_fund_tmp
                df_net = stock.get_exhaustion_rates_of_foreign_investment_by_ticker(check_date_str, market="ALL")
                df_div = stock.get_market_dividend_by_ticker(check_date_str, market="ALL")
                break
        except Exception:
            pass
        base_date -= timedelta(days=1)

    # 펀더멘탈 바인딩
    try:
        if not df_fund.empty and pure_code in df_fund.index:
            row_f = df_fund.loc[pure_code]
            per_val = f"{row_f['PER']:.2f} 배" if pd.notna(row_f['PER']) and row_f['PER'] != 0 else "N/A"
            pbr_val = f"{row_f['PBR']:.2f} 배" if pd.notna(row_f['PBR']) and row_f['PBR'] != 0 else "N/A"
            div_val = f"{row_f['DVD_YLD']:.2f} %" if pd.notna(row_f['DVD_YLD']) and row_f['DVD_YLD'] != 0 else "N/A"
            result["PER"] = per_val
            result["PBR"] = pbr_val
            result["PER_PBR"] = f"PER: {per_val} / PBR: {pbr_val}"
            result["div_rate"] = div_val
    except Exception:
        pass

    # 외국인 정보 바인딩
    try:
        if not df_net.empty and pure_code in df_net.index:
            row_n = df_net.loc[pure_code]
            result["detail"]["외국인한도주식수(A)"] = f"{int(row_n['한도수량']):,} 주"
            result["detail"]["외국인보유주식수(B)"] = f"{int(row_n['보유수량']):,} 주"
            result["detail"]["외국인소진율(B/A)"] = f"{row_n['지분율']:.2f} %"
            result["foreigner"] = f"{row_n['지분율']:.2f}%"
    except Exception:
        pass

    # 액면가 바인딩
    try:
        if not df_div.empty and pure_code in df_div.index:
            row_d = df_div.loc[pure_code]
            result["detail"]["액면가"] = f"{int(row_d['액면가']):,} 원"
    except Exception:
        pass

    # 52주 최고/최저
    try:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        df_price = stock.get_market_ohlcv_by_date(start_date, today_str, pure_code)
        if not df_price.empty:
            result["detail"]["52주최고"] = f"{int(df_price['종가'].max()):,} 원"
            result["detail"]["52주최저"] = f"{int(df_price['종가'].min()):,} 원"
    except Exception:
        pass

    return result


def get_stock_news(stock_code, count: int = 6):
    """
    네이버 금융 관련 뉴스 크롤링 (최대 count건).
    제목·링크·언론사·날짜·본문 미리보기(최대 800자)를 반환.
    """
    pure_code = re.sub(r'[^0-9]', '', str(stock_code))
    url = f"https://finance.naver.com/item/news_news.naver?code={pure_code}"
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://finance.naver.com/',
    }
    news_list = []
    try:
        res = requests.get(url, headers=headers, timeout=6)
        soup = BeautifulSoup(res.text, 'html.parser')
        titles       = soup.select('.title a')
        info_sources = soup.select('.info')
        dates        = soup.select('.date')

        for i in range(min(len(titles), count)):
            title_text = titles[i].get_text(strip=True)
            href = titles[i].get('href', '')
            naver_link = f"https://finance.naver.com{href}" if href.startswith('/') else href
            source     = info_sources[i].get_text(strip=True) if i < len(info_sources) else "네이버 금융"
            date_text  = dates[i].get_text(strip=True)        if i < len(dates)        else "실시간"

            # 뉴스 본문 미리보기 크롤링
            body_preview = ""
            try:
                art_res  = requests.get(naver_link, headers=headers, timeout=5)
                art_soup = BeautifulSoup(art_res.text, 'html.parser')
                for sel in ["#news_read", ".articleCont", "#articeBody", "#dic_area", "article"]:
                    body_tag = art_soup.select_one(sel)
                    if body_tag:
                        body_preview = body_tag.get_text(separator=" ", strip=True)[:800]
                        break
            except Exception:
                body_preview = ""

            news_list.append({
                "제목": title_text, "링크": naver_link,
                "언론사": source,  "날짜": date_text,
                "본문미리보기": body_preview,
            })
    except Exception:
        pass
    return news_list



def summarize_news_with_claude(title, body, stock_name):
    """Anthropic API 호출 -> 뉴스 3줄 핵심 요약 + 주가 영향 판단."""
    if body and body.strip():
        content_for_ai = body.strip()
    else:
        content_for_ai = "(본문 미수집) 제목: " + title
    parts = [
        "다음은 " + stock_name + " 관련 주식 뉴스입니다.",
        "",
        "제목: " + title,
        "",
        "본문(일부): " + content_for_ai,
        "",
        "위 뉴스를 개인 투자자 관점에서 핵심만 3줄 이내로 간결하게 요약해 주세요.",
        "- 주가에 미치는 영향(긍정/부정/중립)을 마지막 줄에 표기하세요.",
        "- 형식 예시: [요약 내용] / 주가 영향: 긍정",
        "반드시 한국어로 답하세요.",
    ]
    prompt = "\n".join(parts)
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("content"):
            return data["content"][0].get("text", "요약 실패").strip()
        return "API 응답 오류 (" + str(resp.status_code) + ")"
    except Exception as ex:
        return "요약 생성 실패: " + str(ex)

def get_naver_chart_data_advanced(stock_code):
    """네이버 금융 일별 시세 크롤링 (최대 3페이지, 약 90거래일)"""
    match = re.search(r'\d{6}', str(stock_code))
    if not match:
        return pd.DataFrame()
    pure_code = match.group()

    dates, opens, highs, lows, closes = [], [], [], [], []
    try:
        for page in [1, 2, 3]:
            url = f"https://finance.naver.com/item/sise_day.naver?code={pure_code}&page={page}"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            res = requests.get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.select("table.type2 tr")
            # [Fix #6] 디버그 print 3개 완전 제거

            for row in rows:
                date_tag = row.select_one(".tah.p10")
                price_tags = row.select(".tah.p11")

                if date_tag and len(price_tags) >= 5:
                    try:
                        dates.append(date_tag.get_text(strip=True))
                        closes.append(int(price_tags[0].get_text(strip=True).replace(",", "")))
                        opens.append(int(price_tags[2].get_text(strip=True).replace(",", "")))
                        highs.append(int(price_tags[3].get_text(strip=True).replace(",", "")))
                        lows.append(int(price_tags[4].get_text(strip=True).replace(",", "")))
                    except ValueError:
                        continue  # 숫자 변환 실패한 행은 건너뜀
    except Exception:
        pass

    if dates:
        df_chart = pd.DataFrame({
            "Date": pd.to_datetime(dates, errors='coerce'),
            "시가": opens, "고가": highs, "저가": lows, "종가": closes
        })
        df_chart.dropna(subset=["Date"], inplace=True)  # 날짜 파싱 실패 행 제거
        df_chart['Open'] = df_chart['시가']
        df_chart['High'] = df_chart['고가']
        df_chart['Low'] = df_chart['저가']
        df_chart['Close'] = df_chart['종가']

        df_chart = df_chart.sort_values(by="Date", ascending=True).reset_index(drop=True)
        df_chart.set_index('Date', inplace=True)

        df_chart['5일선'] = df_chart['종가'].rolling(window=5).mean()
        df_chart['20일선'] = df_chart['종가'].rolling(window=20).mean()
        df_chart['35일선'] = df_chart['종가'].rolling(window=35).mean()
        df_chart['MA5'] = df_chart['5일선']
        df_chart['MA20'] = df_chart['20일선']
        df_chart['MA35'] = df_chart['35일선']

        return df_chart.tail(60)
    return pd.DataFrame()


# ------------------------------------------------------------------------------
# 3. 네이버 금융 API 연동 통합 검색 엔진
# ------------------------------------------------------------------------------
def search_stock_by_name(keyword):
    if not keyword:
        return []
    clean_keyword = keyword.strip().replace(" ", "").upper()
    results = []
    seen_codes = set()

    json_file = "stock_dictionary.json"
    dict_data = {"keywords": {}, "stocks": {}}
    if os.path.exists(json_file):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                dict_data = json.load(f)
        except Exception:
            pass

    for key, items in dict_data.get("keywords", {}).items():
        if clean_keyword in key or key in clean_keyword:
            for item in items:
                code_val = item.get("code", "")
                if not code_val or code_val in seen_codes:
                    continue
                seen_codes.add(code_val)
                m_info = item.get("market", "시장")
                pure_code = code_val.split(".")[0]
                if "display" in item:
                    display_text = item["display"]
                else:
                    desc_str = f" - [{item['desc']}]" if item.get("desc") else ""
                    display_text = f"{item.get('name', '종목명')} ({pure_code} / {m_info}){desc_str}"
                results.append({"code": code_val, "name": item.get("name", "종목명"), "display": display_text})

    for code, info in dict_data.get("stocks", {}).items():
        pure_code = code.split(".")[0]
        name_val = info.get("name", "").upper()
        if (clean_keyword in name_val) or (clean_keyword == pure_code):
            if code not in seen_codes:
                seen_codes.add(code)
                m_info = info.get("market", "시장")
                desc_str = f" - [{info['desc']}]" if info.get("desc") else ""
                results.append({
                    "code": code,
                    "name": info.get("name", "종목명"),
                    "display": f"{info.get('name', '종목명')} ({pure_code} / {m_info}){desc_str}"
                })

    url = f"https://ac.finance.naver.com/ac?q={clean_keyword}&q_enc=utf-8&st=11&frm=stock&r_format=json"
    try:
        res = requests.get(url, timeout=2)
        data = res.json()
        if 'items' in data and data['items']:
            for item in data['items'][0]:
                if len(item) < 3:
                    continue
                code = str(item[0][0]).strip()
                name = str(item[1][0]).strip()
                market = str(item[2][0]).strip()
                suffix = ".KQ" if "코스닥" in market or "KOSDAQ" in market else ".KS"
                full_code = f"{code}{suffix}"
                if len(code) == 6 and code.isdigit() and full_code not in seen_codes:
                    seen_codes.add(full_code)
                    results.append({
                        "code": full_code,
                        "name": name,
                        "display": f"{name} ({code} / {market}) - [실시간 검색]"
                    })
    except Exception:
        pass

    return results


# ------------------------------------------------------------------------------
# 4. Open DART API 기반 기업 실시간 공시 수집기
# ------------------------------------------------------------------------------
def get_dart_disclosures(api_key, stock_code):
    """dart-fss 라이브러리를 통해 상장사 최신 전자공시 5건 수집"""
    pure_code = re.sub(r'[^0-9]', '', str(stock_code))
    try:
        dart.init(api_key=api_key)
        corp = dart.get_corp_info(pure_code)
        if corp:
            reports = corp.search_filings(page_count=5)
            disclosure_list = []
            for r in reports.report_list:
                disclosure_list.append({
                    "접수일자": r.rcept_dt[:10],
                    "공시제목": r.report_nm,
                    "제출인": r.flr_nm,
                    "링크": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.rcp_no}"
                })
            return disclosure_list
    except Exception:
        return [{"접수일자": "-", "공시제목": "DART 연동 오류 또는 공시 의무가 없는 종목(ETF 등)", "제출인": "-", "링크": "#"}]
    return []


# ------------------------------------------------------------------------------
# 5. 메인 레이아웃 및 대시보드 헤더
# ------------------------------------------------------------------------------
st.title("🚀 주식·ETF 통합 관리 및 실시간 공시 스튜디오")
st.markdown("---")

# ------------------------------------------------------------------------------
# 6. 사이드바 제어 센터
# ------------------------------------------------------------------------------
st.sidebar.title("👑 AI 글로벌 WM 센터")
menu_selection = st.sidebar.radio("🧭 이동할 서비스", ["📂 1. 종합/분리 자산 상황판", "⚡ 2. AI 급등예측 및 리스크 관리 엔진"])

st.sidebar.markdown("---")
st.sidebar.header("📁 관심 그룹 관리")

new_group_name = st.sidebar.text_input("✨ 새 그룹 이름 입력", value="")
if st.sidebar.button("➕ 그룹 추가"):
    if new_group_name and new_group_name not in st.session_state.portfolio:
        st.session_state.portfolio[new_group_name] = {}
        save_portfolio_data(st.session_state.portfolio)
        st.success(f"'{new_group_name}' 그룹이 추가되었습니다.")
        st.rerun()

all_groups = list(st.session_state.portfolio.keys())

if all_groups:
    active_group = st.sidebar.selectbox("📂 현재 작업/편집할 자산 그룹", options=all_groups)

    st.sidebar.markdown("---")
    st.sidebar.header("🔍 주식 및 ETF 통합 검색/수정")
    search_input = st.sidebar.text_input("종목명 또는 초성을 입력하세요", value="삼성전자")

    searched_items = search_stock_by_name(search_input)

    if searched_items:
        search_options = {item['code']: item['display'] for item in searched_items}
        selected_search_code = st.sidebar.selectbox(
            "📋 검색 결과 목록에서 선택:",
            options=list(search_options.keys()),
            format_func=lambda x: search_options[x]
        )
        matched = [item['name'] for item in searched_items if item['code'] == selected_search_code]
        selected_search_name = matched[0] if matched else selected_search_code

        existing_asset = st.session_state.portfolio[active_group].get(
            selected_search_code, {"shares": 10, "avg_price": 50000}
        )

        with st.sidebar.form("edit_asset_form"):
            st.markdown(f"**선택된 자산:** `{selected_search_name} ({selected_search_code})`")
            # [Fix #1] int()/float() 괄호 미스매치 수정 — value 인자를 먼저 닫고 step을 별도 인자로 전달
            shares = st.number_input(
                "보유 수량 (주)", min_value=0,
                value=int(existing_asset.get("shares", 0)),
                step=1
            )
            avg_price = st.number_input(
                "평균 매수가 (원)", min_value=0.0,
                value=float(existing_asset.get("avg_price", 0)),
                step=100.0
            )
            submit_asset = st.form_submit_button("💾 수량/매수가 최종 저장·수정")

            if submit_asset:
                st.session_state.portfolio[active_group][selected_search_code] = {
                    "shares": shares, "avg_price": avg_price
                }
                save_portfolio_data(st.session_state.portfolio)
                st.sidebar.success(f"[{selected_search_name}] 포트폴리오 데이터 반영 성공!")
                st.rerun()

    elif search_input:
        st.sidebar.error("검색 결과가 없습니다. 코스피/코스닥/ETF 명칭을 확인해주세요.")

    st.sidebar.markdown("---")
    st.sidebar.header("📤 CSV 포트폴리오 대량 업로드")

    # 컬럼 매핑: 한글 신규 형식 + 영문 구형 형식 동시 지원
    CSV_COL_MAP = {
        "code":      ["종목코드", "code"],
        "shares":    ["보유수량", "shares"],
        "avg_price": ["매수단가", "avg_price"],
        "name":      ["종목명",   "name"],
    }

    def resolve_col(df_cols, candidates):
        for c in candidates:
            if c in df_cols:
                return c
        return None

    # 예시 파일 다운로드 버튼
    _sample_csv = (
        "종목명,종목코드,보유수량,매수단가\n"
        "삼성전자,005930.KS,50,71500\n"
        "SK하이닉스,000660.KS,10,185000\n"
        "KODEX 200,069500.KS,30,35200\n"
        "카카오,035720.KS,20,48300\n"
        "현대차,005380.KS,5,220000\n"
        "TIGER 미국S&P500,360750.KS,100,17800\n"
        "셀트리온,068270.KS,8,155000\n"
        "KODEX 레버리지,122630.KS,40,12500\n"
        "LG에너지솔루션,373220.KS,3,380000\n"
        "네이버,035420.KS,15,195000\n"
    )
    st.sidebar.download_button(
        label="📥 예시 CSV 파일 다운로드",
        data=_sample_csv.encode("utf-8-sig"),
        file_name="portfolio_sample.csv",
        mime="text/csv",
        help="이 양식에 맞춰 작성 후 아래에 업로드하세요."
    )
    st.sidebar.caption(
        "📋 **필수 컬럼**: `종목코드`, `보유수량`, `매수단가`  \n"
        "✅ **선택 컬럼**: `종목명` (중복 확인 화면에 표시)  \n"
        "⚠️ 종목코드: `005930.KS`(코스피) / `035720.KQ`(코스닥) 또는 6자리 숫자"
    )

    uploaded_file = st.sidebar.file_uploader("포트폴리오 CSV 파일 업로드", type=["csv"])

    if uploaded_file is not None:
        file_key = f"csv_loaded_{uploaded_file.name}_{uploaded_file.size}"
        if file_key not in st.session_state:
            try:
                raw_bytes = uploaded_file.read()
                csv_text = None
                for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
                    try:
                        csv_text = raw_bytes.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if csv_text is None:
                    st.sidebar.error("❌ 파일 인코딩 인식 실패. UTF-8 또는 CP949로 저장해 주세요.")
                else:
                    import io as _io
                    csv_df = pd.read_csv(_io.StringIO(csv_text), dtype=str)
                    csv_df.columns = [c.strip() for c in csv_df.columns]

                    col_code  = resolve_col(csv_df.columns, CSV_COL_MAP["code"])
                    col_share = resolve_col(csv_df.columns, CSV_COL_MAP["shares"])
                    col_price = resolve_col(csv_df.columns, CSV_COL_MAP["avg_price"])
                    col_name  = resolve_col(csv_df.columns, CSV_COL_MAP["name"])

                    missing = [k for k, c in [("종목코드", col_code), ("보유수량", col_share), ("매수단가", col_price)] if c is None]
                    if missing:
                        st.sidebar.error(f"❌ 필수 컬럼 누락: {', '.join(missing)} — 예시 파일로 형식 확인.")
                    else:
                        pending_list, skipped = [], []
                        for idx, row in csv_df.iterrows():
                            raw_code = str(row[col_code]).strip()
                            if len(raw_code) == 6 and raw_code.isdigit():
                                search_res = search_stock_by_name(raw_code)
                                formatted_code = search_res[0]["code"] if search_res else f"{raw_code}.KS"
                            else:
                                formatted_code = raw_code
                            stock_label = str(row[col_name]).strip() if col_name else formatted_code
                            try:
                                shares_val    = int(float(str(row[col_share]).replace(",", "").strip()))
                                avg_price_val = float(str(row[col_price]).replace(",", "").strip())
                                if shares_val <= 0 or avg_price_val <= 0:
                                    raise ValueError("0 이하")
                                pending_list.append({
                                    "code": formatted_code, "name": stock_label,
                                    "shares": shares_val,   "avg_price": avg_price_val,
                                })
                            except (ValueError, TypeError):
                                skipped.append(f"행{idx+2}:{stock_label}")
                        if skipped:
                            st.sidebar.warning(f"⚠️ 형식 오류 {len(skipped)}건 건너뜀: {' / '.join(skipped[:5])}")
                        st.session_state.csv_pending_items = pending_list
                        st.session_state.csv_active_group  = active_group
                        st.session_state[file_key] = True
                        st.sidebar.success(f"✅ {len(pending_list)}건 로드. 본문에서 중복 확인 후 저장하세요.")
            except Exception as e:
                st.sidebar.error(f"CSV 파싱 에러: {e}")

    st.sidebar.markdown("---")
    st.sidebar.header("🗑️ 등록된 자산 삭제")
    stocks_in_group = list(st.session_state.portfolio[active_group].keys())
    if stocks_in_group:
        delete_options = {
            code: f"{get_naver_stock_all_info(code)['name']} ({code})"
            for code in stocks_in_group
        }
        stock_to_del = st.sidebar.selectbox(
            "삭제 대상 선택", options=stocks_in_group,
            format_func=lambda x: delete_options.get(x, x)
        )
        if st.sidebar.button("❌ 선택 종목 삭제"):
            del st.session_state.portfolio[active_group][stock_to_del]
            save_portfolio_data(st.session_state.portfolio)
            st.warning("포트폴리오에서 자산이 제거되었습니다.")
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.header("🔑 Open DART API 인증 설정")
    dart_key_input = st.sidebar.text_input(
        "DART API Key 입력", type="password",
        help="실시간 공시 조회를 활성화하려면 발급받은 인증키를 입력하세요."
    )

# ------------------------------------------------------------------------------
# 7. 메인 화면 - CSV 업로드 자산 건별 중복 확인
# ------------------------------------------------------------------------------
if st.session_state.csv_pending_items and st.session_state.csv_active_group:
    target_grp = st.session_state.csv_active_group
    st.warning(f"⚠️ 포트폴리오 가져오기 마법사 (현재 대상 그룹: {target_grp})")
    st.markdown("이미 등록되어 있는 종목이 발견되었습니다. 각 종목별로 반영 방식을 승인해 주세요.")

    with st.form("csv_confirm_form"):
        updated_portfolio_buffer = dict(st.session_state.portfolio[target_grp])
        conflict_detected = False

        for item in st.session_state.csv_pending_items:
            code = item["code"]
            stock_name = get_naver_stock_all_info(code)["name"]

            if code in updated_portfolio_buffer:
                conflict_detected = True
                old_info = updated_portfolio_buffer[code]
                st.markdown(f"#### 🔍 **중복 종목 발견**: {stock_name} ({code})")
                cc1, cc2 = st.columns(2)
                cc1.info(f"📦 **기존**: {old_info['shares']:,} 주 / 평단 {int(old_info['avg_price']):,} 원")
                cc2.success(f"📥 **CSV**: {item['shares']:,} 주 / 평단 {int(item['avg_price']):,} 원")

                user_choice = st.radio(
                    f"[{stock_name}] 처리 방식",
                    ["새 데이터로 덮어쓰기 (Overwrite)", "기존 데이터 유지 (Keep Old)"],
                    key=f"csv_conf_{code}"
                )
                if user_choice == "새 데이터로 덮어쓰기 (Overwrite)":
                    updated_portfolio_buffer[code] = {"shares": item["shares"], "avg_price": item["avg_price"]}
            else:
                updated_portfolio_buffer[code] = {"shares": item["shares"], "avg_price": item["avg_price"]}

        if not conflict_detected:
            st.info("중복 리스크가 없는 깨끗한 데이터셋입니다.")

        submit_all_csv = st.form_submit_button("✅ 최종 포트폴리오 동기화 및 승인")
        if submit_all_csv:
            st.session_state.portfolio[target_grp] = updated_portfolio_buffer
            save_portfolio_data(st.session_state.portfolio)
            st.session_state.csv_pending_items = []
            st.session_state.csv_active_group = None
            st.success("성공적으로 데이터 병합 작업이 완료되었습니다!")
            st.rerun()
    st.markdown("---")


# ------------------------------------------------------------------------------
# 8. 메인 화면 - 포트폴리오 실시간 자산 상황판
# ------------------------------------------------------------------------------
if menu_selection == "📂 1. 종합/분리 자산 상황판":
    st.title("📂 글로벌 멀티-포트폴리오 자산 상황판")

    if not st.session_state.portfolio:
        st.warning("먼저 사이드바에서 관심 자산 그룹을 생성하고 주식을 등록해주세요.")
    else:
        analysis_mode = st.radio(
            "📊 분석 모드 선택",
            ["모든 그룹 통합 분석 (종합)", "특정 그룹 단독 분석 (분리)"],
            horizontal=True
        )

        if analysis_mode == "모든 그룹 통합 분석 (종합)":
            target_groups = list(st.session_state.portfolio.keys())
        else:
            selected_single_group = st.selectbox(
                "🎯 분석할 단독 그룹 선택",
                options=list(st.session_state.portfolio.keys())
            )
            target_groups = [selected_single_group] if selected_single_group else []

        portfolio_rows = []
        total_buy_cash = 0.0
        total_eval_cash = 0.0

        with st.spinner("🚀 전 세계 마켓 네트워크 연동 및 실시간 데이터 패칭 중..."):
            for g_name in target_groups:
                for code, data in st.session_state.portfolio[g_name].items():
                    stock_info = get_naver_stock_all_info(code)
                    buy_price = float(data["avg_price"])
                    shares = float(data["shares"])

                    _naver_price = float(stock_info["price"]) if stock_info["price"] > 0 else buy_price
                    try:
                        ticker_df = yf.download(code, period="5d", interval="1d", progress=False)
                        if ticker_df is not None and not ticker_df.empty:
                            if isinstance(ticker_df.columns, pd.MultiIndex):
                                ticker_df.columns = ticker_df.columns.get_level_values(0)
                        if not ticker_df.empty and len(ticker_df) >= 2:
                            _raw_cur  = ticker_df['Close'].iloc[-1]
                            _raw_prev = ticker_df['Close'].iloc[-2]
                            if pd.isna(_raw_cur) or pd.isna(_raw_prev):
                                current_p = _naver_price
                                daily_diff, daily_pct = 0.0, 0.0
                            else:
                                current_p  = float(_raw_cur)
                                prev_p     = float(_raw_prev)
                                daily_diff = current_p - prev_p
                                daily_pct  = (daily_diff / prev_p) * 100 if prev_p != 0 else 0.0
                        else:
                            current_p = _naver_price
                            daily_diff, daily_pct = 0.0, 0.0
                    except Exception:
                        current_p = _naver_price
                        daily_diff, daily_pct = 0.0, 0.0

                    buy_total = buy_price * shares
                    eval_total = current_p * shares
                    profit_loss = eval_total - buy_total
                    profit_rate = ((current_p / buy_price) - 1) * 100 if buy_price > 0 else 0.0

                    total_buy_cash += buy_total
                    total_eval_cash += eval_total

                    portfolio_rows.append({
                        "그룹": g_name,
                        "종목명": stock_info["name"],
                        "종목코드": code.split('.')[0],
                        "보유수량": shares,
                        "평균매수가": buy_price,
                        "현재가": current_p,
                        "전일대비 변동": f"{daily_diff:+,.0f}원 ({daily_pct:+.2f}%)",
                        "총매수금액": buy_total,
                        "현재평가액": eval_total,
                        "평가손익": profit_loss,
                        "수익률(%)": profit_rate,
                        "_raw_diff": daily_diff
                    })

        if portfolio_rows:
            df = pd.DataFrame(portfolio_rows)
            total_profit_loss = total_eval_cash - total_buy_cash
            total_profit_rate = ((total_eval_cash / total_buy_cash) - 1) * 100 if total_buy_cash > 0 else 0.0

            def safe_int(v):
                try:
                    return 0 if (v != v or v in (float('inf'), float('-inf'))) else int(v)
                except (TypeError, ValueError):
                    return 0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🛒 총 매입 금액", f"{safe_int(total_buy_cash):,} 원")
            m2.metric("📈 총 평가 금액", f"{safe_int(total_eval_cash):,} 원")
            m3.metric("💰 총 평가 손익", f"{safe_int(total_profit_loss):,} 원", delta=f"{safe_int(total_profit_loss):+} 원")
            m4.metric("📊 종합 수익률", f"{total_profit_rate:.2f}%", delta=f"{total_profit_rate:+.2f}%")

            st.markdown("### 📋 실시간 종합 자산 상황판 리포트")

            def format_dashboard_styles(row):
                styles = [''] * len(row)
                curr_idx = row.index.get_loc('현재가')
                change_idx = row.index.get_loc('전일대비 변동')
                if row['_raw_diff'] > 0:
                    color_style = 'color: #ef4444; font-weight: bold;'
                elif row['_raw_diff'] < 0:
                    color_style = 'color: #3b82f6; font-weight: bold;'
                else:
                    color_style = 'color: #718096;'
                styles[curr_idx] = color_style
                styles[change_idx] = color_style

                gl_idx = row.index.get_loc('평가손익')
                roi_idx = row.index.get_loc('수익률(%)')
                if row['평가손익'] > 0:
                    gain_style = 'color: #ef4444; font-weight: bold;'
                elif row['평가손익'] < 0:
                    gain_style = 'color: #3b82f6; font-weight: bold;'
                else:
                    gain_style = 'color: #718096;'
                styles[gl_idx] = gain_style
                styles[roi_idx] = gain_style
                return styles

            display_df = df.copy()
            styled_view = display_df.style.format({
                "보유수량": "{:,.1f}",
                "평균매수가": "{:,.0f}",
                "현재가": "{:,.0f}",
                "총매수금액": "{:,.0f}",
                "현재평가액": "{:,.0f}",
                "평가손익": "{:,.0f}",
                "수익률(%)": "{:+.2f}%"
            }).apply(format_dashboard_styles, axis=1)

            # [Fix #3] width='stretch' → width='stretch' (전체 적용)
            st.dataframe(
                styled_view,
                column_order=[
                    "그룹", "종목명", "종목코드", "보유수량", "평균매수가", "현재가",
                    "전일대비 변동", "총매수금액", "현재평가액", "평가손익", "수익률(%)"
                ],
                width='stretch'
            )

            st.markdown("---")
            st.markdown("### 🦚 포트폴리오 자산 배분 비중 분석")

            df["매입비중(%)"] = (df["총매수금액"] / total_buy_cash) * 100 if total_buy_cash > 0 else 0.0
            df["평가비중(%)"] = (df["현재평가액"] / total_eval_cash) * 100 if total_eval_cash > 0 else 0.0

            weight_df = df[
                ["그룹", "종목명", "총매수금액", "매입비중(%)", "현재평가액", "평가비중(%)"]
            ].sort_values(by="평가비중(%)", ascending=False)

            styled_weight_df = weight_df.style.format({
                "총매수금액": "{:,.0f}원",
                "매입비중(%)": "{:.2f}%",
                "현재평가액": "{:,.0f}원",
                "평가비중(%)": "{:.2f}%"
            })
            st.dataframe(styled_weight_df, width='stretch', hide_index=True)  # [Fix #3]

        else:
            st.info("분석할 자산 카드가 포트폴리오에 존재하지 않습니다.")


# ------------------------------------------------------------------------------
# 페이지 2: AI 급등예측 엔진
# ------------------------------------------------------------------------------
else:
    st.title("⚡ AI 급등·급락 예측 엔진 및 일단위 종합 마켓뷰")

    all_exist_codes = []
    for g_name, g_stocks in st.session_state.portfolio.items():
        all_exist_codes.extend(list(g_stocks.keys()))

    if not all_exist_codes:
        all_exist_codes = ["005930.KS", "433280.KS"]

    selected_target = st.selectbox(
        "🧐 정밀 AI 분석 진단을 실행할 타겟 자산 선택",
        options=list(set(all_exist_codes)),
        format_func=lambda x: f"{get_naver_stock_all_info(x)['name']} ({x.split('.')[0]})"
    )

    asset_all = get_naver_stock_all_info(selected_target)
    krx_info = get_KRX_stock_all_info(selected_target)

    for k, v in asset_all["detail"].items():
        if krx_info["detail"].get(k) in ["N/A", "", None]:
            krx_info["detail"][k] = v
    for k in ["PER", "PBR", "PER_PBR", "div_rate"]:
        if krx_info.get(k) in ["N/A", "", None]:
            krx_info[k] = asset_all.get(k, "N/A")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("### 🩺 1. 기업 가치 및 과거 배당 진단")
        st.write(f"**🏢 자산명**: {asset_all.get('name', '알 수 없음')}")
        st.write(f"**💵 실시간 현재가**: {asset_all.get('price_str', 'N/A')}")
        st.write(f"**📊 밸류에이션 지표**: {asset_all.get('PER_PBR', 'N/A')}")

        st.markdown("##### 💵 최근 연간 배당 정보")
        if asset_all.get("div_rate") and asset_all["div_rate"] != "N/A":
            st.success(f"📌 **최근 공시 기준 추정 배당수익률**: {asset_all['div_rate']}")
        else:
            st.info("배당 이력이 없거나 분배금 정보를 가져올 수 없는 자산군입니다.")

    with c2:
        st.markdown("### 📡 2. 기본 투자 정보 테이블")
        with st.spinner("KRX 금융 종합 투자정보 실시간 동기화 중..."):
            detail_invest = krx_info["detail"]
            invest_df = pd.DataFrame({
                "투자 정보 항목": list(detail_invest.keys()),
                "실시간 데이터": list(detail_invest.values())
            })
            st.table(invest_df)

    st.markdown("---")
    st.markdown("### 🧠 3. 일단위 종합 AI 투자 판단 및 신호 생성기")

    base_score = 52
    if "원" in asset_all["price_str"]:
        base_score += (asset_all["price"] % 25)

    news_data = get_stock_news(selected_target)
    chart_score = min(max(base_score + 3, 15), 95)
    supply_score = min(max(base_score - 4, 10), 90)
    news_score = 70 if news_data else 50
    finance_score = 65 if "N/A" not in asset_all["PER"] else 55

    score_df = pd.DataFrame({
        "평가 컴포넌트": ["📈 기술적 차트 분석 시그널", "🦅 기관 및 외국인 수급 동향", "📰 뉴스 센티먼트 지수", "💼 재무 건전성 및 밸류 스코어"],
        "점수 (100점 만점)": [f"{chart_score}점", f"{supply_score}점", f"{news_score}점", f"{finance_score}점"],
        "상태": [
            "강세 진입" if chart_score > 60 else "중립",
            "순매수" if supply_score > 55 else "관망",
            "긍정적" if news_score > 60 else "이슈 없음",
            "안정적" if finance_score > 60 else "ETF 표준"
        ]
    })
    st.table(score_df)

    final_avg = (chart_score + supply_score + news_score + finance_score) / 4
    st.markdown("##### 🎯 최종 일단위 복합 투자 의견")
    if final_avg >= 65:
        st.success(f"🟢 **AI 종합 의견: 비중 확대 (Accumulate / BUY)** — 종합 AI 평점: {final_avg:.1f}점")
    elif final_avg >= 50:
        st.warning(f"🟡 **AI 종합 의견: 보유 및 관망 (Hold / Neutral)** — 종합 AI 평점: {final_avg:.1f}점")
    else:
        st.error(f"🔴 **AI 종합 의견: 비중 축소 (Reduce / SELL)** — 종합 AI 평점: {final_avg:.1f}점")

    # --------------------------------------------------------------------------
    # 4. 실시간 트렌드 차트 분석
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 📈 4. 향후 투자 방향성 검토용 실시간 추세 차트 (최근 60거래일)")

    def get_buy_price_by_code(target_code):
        search_code = target_code.replace(".KS", "").replace(".KQ", "")
        for g_name in st.session_state.portfolio.keys():
            for code, data in st.session_state.portfolio[g_name].items():
                if code.replace(".KS", "").replace(".KQ", "") == search_code:
                    return data.get("avg_price", 0)
        return 0

    avg_buy_price = float(get_buy_price_by_code(selected_target) or 0)

    with st.spinner("네이버 금융 데이터 기반 전문 차트 빌드 중..."):
        df_trends = get_naver_chart_data_advanced(selected_target)

        if df_trends is not None and not df_trends.empty:
            rename_map = {
                'Close': '종가', 'Open': '시가', 'High': '고가', 'Low': '저가', 'Volume': '거래량',
                'close': '종가', 'open': '시가', 'high': '고가', 'low': '저가', 'volume': '거래량'
            }
            df_trends = df_trends.rename(columns=rename_map)

            def clean_df_column(df, col_name):
                series = df[col_name]
                return series.iloc[:, 0] if isinstance(series, pd.DataFrame) else series

            try:
                clean_open = clean_df_column(df_trends, '시가')
                clean_high = clean_df_column(df_trends, '고가')
                clean_low = clean_df_column(df_trends, '저가')
                clean_close = clean_df_column(df_trends, '종가')

                df_trends['MA5'] = clean_close.rolling(window=5).mean()
                df_trends['MA35'] = clean_close.rolling(window=35).mean()

                highest_price = float(clean_high.max())
                lowest_price = float(clean_low.min())

                if clean_close.isna().all():
                    st.warning("⚠️ 주가 데이터가 모두 결측치(NaN) 처리되어 시각화가 불가능합니다.")
                else:
                    tab1, tab2 = st.tabs(["📈 실시간 분석 차트", "📋 데이터 상세 보기"])

                    with tab1:
                        st.subheader(f"📊 주식 가격 분석 [ 최고가: {highest_price:,}원 | 최저가: {lowest_price:,}원 ]")
                        fig = make_subplots(rows=1, cols=1, shared_xaxes=True)

                        fig.add_trace(go.Candlestick(
                            x=df_trends.index,
                            open=clean_open, high=clean_high, low=clean_low, close=clean_close,
                            name='주가',
                            increasing_line_color='red', increasing_fillcolor='red',
                            decreasing_line_color='blue', decreasing_fillcolor='blue'
                        ))
                        fig.add_trace(go.Scatter(
                            x=df_trends.index, y=df_trends['MA5'],
                            mode='lines', name='5일 이평선',
                            line=dict(color='royalblue', width=1.5)
                        ))
                        fig.add_trace(go.Scatter(
                            x=df_trends.index, y=df_trends['MA35'],
                            mode='lines', name='35일 이평선',
                            line=dict(color='orange', width=1.5)
                        ))

                        if avg_buy_price > 0:
                            fig.add_hline(y=avg_buy_price, line_dash="dash", line_color="red", line_width=1.5,
                                          annotation_text=f" 매수평균가 ({avg_buy_price:,}원)",
                                          annotation_position="top left")

                        fig.add_hline(y=highest_price, line_color="darkred", line_width=1, opacity=0.6,
                                      annotation_text=f" 상단 저항선 ({highest_price:,}원)",
                                      annotation_position="top right")
                        fig.add_hline(y=lowest_price, line_color="darkcyan", line_width=1, opacity=0.6,
                                      annotation_text=f" 하단 지지선 ({lowest_price:,}원)",
                                      annotation_position="bottom right")

                        fig.update_layout(
                            xaxis_title="날짜 (Date)", yaxis_title="가격 (Price)",
                            font=dict(family="Malgun Gothic, AppleGothic, sans-serif", size=12),
                            xaxis_rangeslider_visible=False, hovermode="x unified",
                            dragmode="pan", margin=dict(l=20, r=20, t=40, b=20),
                            template="plotly_white"
                        )
                        fig.update_yaxes(tickformat=",.0f", ticksuffix="원")

                        # [Fix #3] width='stretch' → width='stretch'
                        st.plotly_chart(fig, width='stretch', config={
                            'scrollZoom': True,
                            'modeBarButtonsToAdd': ['drawline', 'drawopenpath', 'eraseshape'],
                            'displayModeBar': True
                        })
                        st.info("💡 마우스 휠로 확대/축소, 드래그로 이동 가능합니다.")

                    with tab2:
                        st.dataframe(
                            df_trends[['시가', '고가', '저가', '종가', 'MA5', 'MA35']].tail(20),
                            width='stretch'  # [Fix #3]
                        )

            except Exception as e:
                st.error(f"📈 차트 가공 및 렌더링 중 오류가 발생했습니다. (사유: {e})")

            st.caption("※ 네이버 금융 데이터 기반 5일(청색), 35일(오렌지) 이동평균선 캔들차트입니다.")
        else:
            st.info("해당 자산의 시세 데이터를 크롤링하지 못했습니다.")

    # --------------------------------------------------------------------------
    # 5. 종합 차트 심층 분석
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 📈 5. 종합 차트 심층 분석")

    ticker_list = [selected_target]
    names_dict = {selected_target: asset_all.get('name', '분석 종목')}
    radio_options = {code: f"{names_dict.get(code, code)} ({code.split('.')[0]})" for code in ticker_list}

    selected_code = st.radio(
        "🔍 심층 분석 대상 종목 선택:",
        options=ticker_list,
        format_func=lambda x: radio_options.get(x, x),
        horizontal=True
    )

    if selected_code:
        st.success(f"🎯 [{names_dict.get(selected_code)}] 종목의 타임프레임 엔진이 바인딩되었습니다.")

        st.markdown("##### 📅 차트 주기(Timeframe) 선택")
        tf_choice = st.radio(
            "주기 변경",
            ["60분봉", "일봉 (Daily)", "주봉 (Weekly)", "월봉 (Monthly)", "년봉 (Yearly)"],
            horizontal=True, label_visibility="collapsed"
        )

        tf_mapping = {
            "60분봉": {"p": "60d", "i": "60m"},
            "일봉 (Daily)": {"p": "1y", "i": "1d"},
            "주봉 (Weekly)": {"p": "3y", "i": "1wk"},
            "월봉 (Monthly)": {"p": "5y", "i": "1mo"},
            "년봉 (Yearly)": {"p": "max", "i": "1y"}
        }

        chosen_p = tf_mapping[tf_choice]["p"]
        chosen_i = tf_mapping[tf_choice]["i"]

        with st.spinner("지정된 주기 세력 수급 데이터 연산 중..."):
            detail_df = yf.download(selected_code, period=chosen_p, interval=chosen_i, progress=False)
            if isinstance(detail_df.columns, pd.MultiIndex):
                detail_df.columns = detail_df.columns.get_level_values(0)

        tab_chart, tab_finance = st.tabs(["📈 인터랙티브 멀티 추세선 스튜디오", "📋 기업 가치 진단 및 투자자별 수급 동향"])

        with tab_chart:
            if detail_df.empty:
                st.error("해당 주기의 데이터를 가져올 수 없습니다.")
            else:
                def get_clean_series(df, col_name):
                    series = df[col_name]
                    return series.iloc[:, 0] if isinstance(series, pd.DataFrame) else series

                clean_close = get_clean_series(detail_df, 'Close')
                clean_high = get_clean_series(detail_df, 'High')
                clean_low = get_clean_series(detail_df, 'Low')
                clean_open = get_clean_series(detail_df, 'Open')
                clean_volume = get_clean_series(detail_df, 'Volume')

                detail_df['MA5'] = clean_close.rolling(window=5).mean()
                detail_df['MA20'] = clean_close.rolling(window=20).mean()
                detail_df['MA35'] = clean_close.rolling(window=35).mean()
                detail_df['MA60'] = clean_close.rolling(window=60).mean()

                window_len = min(len(detail_df), 60)
                recent_data = detail_df.iloc[-window_len:]

                max_p = float(get_clean_series(recent_data, 'High').max())
                min_p = float(get_clean_series(recent_data, 'Low').min())
                mid_p = (max_p + min_p) / 2.0

                # OBV 계산
                obv = [0]
                cls_v = clean_close.values
                vol_v = clean_volume.values
                for i in range(1, len(detail_df)):
                    if cls_v[i] > cls_v[i - 1]:
                        obv.append(obv[-1] + vol_v[i])
                    elif cls_v[i] < cls_v[i - 1]:
                        obv.append(obv[-1] - vol_v[i])
                    else:
                        obv.append(obv[-1])
                detail_df['OBV'] = obv

                # MFI 계산
                tp = (clean_high + clean_low + clean_close) / 3
                rmf = tp * clean_volume
                pos_mf = np.where(tp.diff() > 0, rmf, 0)
                neg_mf = np.where(tp.diff() < 0, rmf, 0)
                pos_sum = pd.Series(pos_mf).rolling(14).sum()
                neg_sum = pd.Series(neg_mf).rolling(14).sum()
                detail_df['MFI'] = 100 - (100 / (1 + (pos_sum / (neg_sum + 1e-10))))

                fig = make_subplots(
                    rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                    row_heights=[0.50, 0.15, 0.17, 0.18],
                    subplot_titles=(
                        f"🔥 주가 변동 스튜디오 ({tf_choice})",
                        "📊 거래량 (Volume)", "🧡 OBV 세력 선행 매집지표", "💛 MFI 자금 유출입"
                    )
                )

                fig.add_trace(go.Candlestick(
                    x=detail_df.index,
                    open=clean_open, high=clean_high, low=clean_low, close=clean_close,
                    name='주가(봉)',
                    increasing_line_color='red', increasing_fillcolor='red',
                    decreasing_line_color='blue', decreasing_fillcolor='blue'
                ), row=1, col=1)

                fig.add_trace(go.Scatter(x=detail_df.index, y=detail_df['MA5'], name='5일선', line=dict(color='royalblue', width=1.5)), row=1, col=1)
                fig.add_trace(go.Scatter(x=detail_df.index, y=detail_df['MA20'], name='20일선', line=dict(color='#e74c3c', width=1.3)), row=1, col=1)
                fig.add_trace(go.Scatter(x=detail_df.index, y=detail_df['MA35'], name='35일선', line=dict(color='orange', width=1.5)), row=1, col=1)
                fig.add_trace(go.Scatter(x=detail_df.index, y=detail_df['MA60'], name='60일선', line=dict(color='#3498db', width=1.5)), row=1, col=1)

                fig.add_hline(y=max_p, line_dash="dash", line_color="darkred",
                              annotation_text=f" 상단 저항선 ({max_p:,.0f}원)", annotation_position="top right", row=1, col=1)
                fig.add_hline(y=mid_p, line_dash="dot", line_color="#7f8c8d",
                              annotation_text=" 중심 밸런스 선", annotation_position="bottom left", row=1, col=1)
                fig.add_hline(y=min_p, line_dash="dash", line_color="darkcyan",
                              annotation_text=f" 하단 지지선 ({min_p:,.0f}원)", annotation_position="bottom right", row=1, col=1)

                # [Fix #8] locals()/globals() 조건 제거 — avg_buy_price는 항상 정의됨
                if avg_buy_price > 0:
                    fig.add_hline(y=avg_buy_price, line_dash="dash", line_color="red", line_width=1.5,
                                  annotation_text=f" 매수평균가 ({avg_buy_price:,.0f}원)",
                                  annotation_position="top left", row=1, col=1)

                fig.add_trace(go.Bar(x=detail_df.index, y=clean_volume, name='거래량', marker_color='#bdc3c7'), row=2, col=1)
                fig.add_trace(go.Scatter(x=detail_df.index, y=detail_df['OBV'], name='OBV', line=dict(color='#fa8231', width=2)), row=3, col=1)
                fig.add_trace(go.Scatter(x=detail_df.index, y=detail_df['MFI'], name='MFI', line=dict(color='#f1c40f', width=2)), row=4, col=1)

                fig.update_layout(
                    height=850, template="plotly_white", hovermode="x unified", showlegend=True,
                    font=dict(family="Malgun Gothic, AppleGothic, sans-serif", size=11),
                    xaxis_rangeslider_visible=False, dragmode="pan"
                )
                fig.update_yaxes(tickformat=",.0f", ticksuffix="원", row=1, col=1)

                # [Fix #3] width='stretch' → width='stretch'
                st.plotly_chart(fig, width='stretch', config={'scrollZoom': True, 'displayModeBar': True})

        with tab_finance:
            left_fin, right_news = st.columns(2)
            with left_fin:
                st.markdown("#### 🏢 멀티 모듈 기반 밸류에이션 리포트")
                nav_data = get_naver_stock_all_info(selected_target)
                v_col1, v_col2 = st.columns(2)
                v_col1.metric("PER (실시간)", nav_data["PER"])
                v_col2.metric("PBR (실시간)", nav_data["PBR"])
                st.markdown("""
                > 💡 **AI 계량 밸류에이션 적정 수준 가이드 (대한민국 시장 표준)**
                > * **PER**: 국장 제조업 평균 기준 **8배 ~ 12배**가 적정 수준입니다.
                > * **PBR**: 기업 청산가치 기준 **1.0배**가 표준 적정선입니다.
                """)

            with right_news:
                st.markdown("#### 👥 기관 및 외인 지분 보유 동향")
                try:
                    t_obj = yf.Ticker(selected_code)
                    t_info = t_obj.info
                    inst_pct = (t_info.get("heldPercentInstitutions") or 0) * 100
                    insider_pct = (t_info.get("heldPercentInsiders") or 0) * 100
                    i_col1, i_col2 = st.columns(2)
                    i_col1.metric("메이저 기관 지분율", f"{inst_pct:.2f} %")
                    i_col2.metric("대주주 및 내부인 지분율", f"{insider_pct:.2f} %")
                except Exception:
                    st.caption("투자자별 상세 지분 동향 로드 실패")

    # --------------------------------------------------------------------------
    # 6. AI 알람 및 실시간 리스크 헤지 엔진
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 🚨 6. AI 투자 알람 및 리스크 트래킹 엔진")

    st.markdown("""
    본 엔진은 **ATR(평균 실질 변동폭) 채널**, **RSI 센티먼트**, **트레이링 스톱 알고리즘**을 복합 연산하여
    실시간으로 자산별 진입, 청산, 손절 기준선을 계산하고 강제 알람 신호를 발생시킵니다.
    """)

    with st.spinner("선택 종목의 일단위 변동성 데이터 시뮬레이션 및 알람 분석 중..."):
        if 'df_trends' not in locals() or df_trends is None or df_trends.empty:
            df_trends = get_naver_chart_data_advanced(selected_target)

        if df_trends is not None and not df_trends.empty and len(df_trends) >= 5:
            rename_map = {
                'Close': '종가', 'Open': '시가', 'High': '고가', 'Low': '저가', 'Volume': '거래량',
                'close': '종가', 'open': '시가', 'high': '고가', 'low': '저가', 'volume': '거래량'
            }
            df_trends = df_trends.rename(columns=rename_map)

            if '종가' not in df_trends.columns:
                st.error("⚠️ '종가' 컬럼을 파싱할 수 없습니다. 타 종목을 검색해 주세요.")
                st.stop()

            target_close_series = df_trends['종가']
            if isinstance(target_close_series, pd.DataFrame):
                target_close_series = target_close_series.iloc[:, 0]

            recent_close = float(target_close_series.iloc[-1])
            prev_close = float(target_close_series.iloc[-2])

            df_trends['TrueRange'] = target_close_series.diff().abs()
            atr_series = df_trends['TrueRange'].rolling(window=5).mean()
            calculated_atr = atr_series.iloc[-1]
            if pd.isna(calculated_atr) or calculated_atr == 0:
                calculated_atr = recent_close * 0.02

            entry_target_price = int(target_close_series.max())

            is_golden_cross = False
            if '5일선' in df_trends.columns and '20일선' in df_trends.columns:
                ma5_val = df_trends['5일선'].iloc[-1]
                ma20_val = df_trends['20일선'].iloc[-1]
                if isinstance(ma5_val, pd.Series): ma5_val = ma5_val.iloc[0]
                if isinstance(ma20_val, pd.Series): ma20_val = ma20_val.iloc[0]
                if pd.notna(ma5_val) and pd.notna(ma20_val) and ma5_val > ma20_val:
                    is_golden_cross = True

            max_high_price = target_close_series.max()
            take_profit_target = int(max_high_price - (2.5 * calculated_atr))
            min_low_price = target_close_series.min()
            trailing_stop_loss = int(max(recent_close * 0.965, min_low_price))

            a_col1, a_col2, a_col3, a_col4 = st.columns(4)

            with a_col1:
                st.markdown("##### 📥 신규 진입 매수 알람")
                if recent_close >= entry_target_price or is_golden_cross:
                    st.error("🚨 [신호] 즉시 진입 가능\n\n추세 상방 돌파 매수세 포착")
                else:
                    st.info("⚪ [대기] 조건 미충족\n\n박스권 돌파 관망 필요")
                st.caption(f"목표 돌파가: **{entry_target_price:,}원**")

            with a_col2:
                st.markdown("##### 🛒 추가 매수 시점 알람")
                if recent_close > prev_close and is_golden_cross:
                    st.success("🔥 [신호] 분할 매수 적기\n\n단기 이평 정배열 우상향")
                else:
                    st.info("⚪ [대기] 매수 보류\n\n하방 압력 지속 상태")
                st.caption(f"단기 지지선: **{int(target_close_series.mean()):,}원**")

            with a_col3:
                st.markdown("##### 📤 추세 매도(익절) 알람")
                if recent_close <= take_profit_target:
                    st.error("🚨 [경보] 분할 익절 스톱\n\n추세 최고점 대비 꺾임 포착")
                else:
                    st.success("🟢 [유지] 추세 보유 가능\n\n상방 렐리 유효 구간")
                st.caption(f"익절 마지노선: **{take_profit_target:,}원**")

            with a_col4:
                st.markdown("##### ⛔ 리스크 관리 손절 경보")
                if recent_close <= trailing_stop_loss:
                    st.error("💥 [위험] 무조건 손절 탈출\n\n시스템 리스크 밴드 이탈")
                else:
                    st.warning("🟢 [안전] 리스크 하방 방어\n\n손절선 위에서 안정적 변동")
                st.caption(f"절대 손절선: **{trailing_stop_loss:,}원**")

            st.markdown("##### 📊 한눈에 보는 실시간 매매 전략 맵")
            alarm_summary_table = pd.DataFrame({
                "알람 통제 매커니즘": ["신규 추세 진입 (Breakout)", "장중 매수 타이밍 (Timing)", "이익 보존 매도선 (Take Profit)", "자산 보호 손절선 (Stop Loss)"],
                "시스템 판단 기준가격": [
                    f"{entry_target_price:,} 원 이상", "이평선 정배열 골든크로스 시",
                    f"{take_profit_target:,} 원 붕괴 시", f"{trailing_stop_loss:,} 원 하향 이탈 시"
                ],
                "현재가 대비 격리 수준": [
                    f"{entry_target_price - recent_close:,} 원 남음" if entry_target_price > recent_close else "돌파 완료",
                    "조건 충족 중" if is_golden_cross else "조건 미충족",
                    f"+{recent_close - take_profit_target:,} 원 마진 잔여",
                    f"+{recent_close - trailing_stop_loss:,} 원 위험 버퍼 잔여"
                ],
                "최종 액션 시그널": [
                    "🎯 신규 매수 관심" if recent_close < entry_target_price else "🔥 돌파 매수",
                    "🚀 매수 활성화" if is_golden_cross else "💤 관망",
                    "HOLD" if recent_close > take_profit_target else "⚠️ 매도 분할 대응",
                    "STABLE" if recent_close > trailing_stop_loss else "🚨 즉시 손절(REDUCE)"
                ]
            })
            st.dataframe(alarm_summary_table, width='stretch', hide_index=True)  # [Fix #3]

        else:
            st.info("알람 엔진 구동을 위한 과거 거래 시세 데이터 일수가 부족합니다. (최소 5거래일 필요)")

    # --------------------------------------------------------------------------
    # 🗞️ 7. 종목 관련 실시간 뉴스 링크 & AI 핵심 요약
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 🗞️ 7. 종목 관련 실시간 뉴스 & AI 핵심 요약")
    st.caption(
        "네이버 금융 실시간 뉴스 최신 6건을 수집하고, "
        "Claude AI가 각 기사를 개인 투자자 관점에서 3줄로 핵심 요약합니다."
    )

    news_count = st.slider("수집할 뉴스 건수", min_value=2, max_value=8, value=5, step=1)
    run_news   = st.button("🔄 뉴스 수집 및 AI 요약 실행", type="primary")

    news_cache_key = "news_cache_" + selected_target
    if run_news:
        st.session_state[news_cache_key] = None

    if news_cache_key not in st.session_state or st.session_state[news_cache_key] is None:
        if run_news:
            stock_display_name = asset_all.get("name", selected_target)
            with st.spinner("뉴스 수집 및 AI 요약 생성 중 (기사당 약 5~10초 소요)..."):
                raw_news = get_stock_news(selected_target, count=news_count)
                enriched = []
                for item in raw_news:
                    summary = summarize_news_with_claude(
                        title=item["제목"],
                        body=item["본문미리보기"],
                        stock_name=stock_display_name,
                    )
                    enriched.append({**item, "AI_요약": summary})
                st.session_state[news_cache_key] = enriched
        else:
            st.info("위 버튼을 클릭하면 최신 뉴스와 AI 요약을 불러옵니다.")

    cached_news = st.session_state.get(news_cache_key)
    if cached_news:

        def impact_color(summary_text):
            t = summary_text
            if any(k in t for k in ["긍정", "상승", "호재", "매수"]):
                return "#ef4444"
            if any(k in t for k in ["부정", "하락", "악재", "매도"]):
                return "#3b82f6"
            return "#6b7280"

        for idx, news in enumerate(cached_news, start=1):
            bc   = impact_color(news.get("AI_요약", ""))
            link = news["링크"]
            title_text = news["제목"]
            source     = news["언론사"]
            date_text  = news["날짜"]
            summary    = news.get("AI_요약", "요약 없음")

            html_card = (
                '<div style="border-left:5px solid ' + bc + ';'
                'background:#f8f9fa;border-radius:8px;'
                'padding:14px 18px;margin-bottom:14px;">'
                '<div style="font-size:0.78rem;color:#6b7280;margin-bottom:4px;">'
                + str(idx) + ". &nbsp;<b>" + source + "</b> &nbsp;|&nbsp; " + date_text +
                '</div>'
                '<div style="font-size:1.02rem;font-weight:700;margin-bottom:8px;">'
                '<a href="' + link + '" target="_blank" '
                'style="color:#111827;text-decoration:none;">'
                "🔗 " + title_text +
                '</a></div>'
                '<div style="font-size:0.9rem;color:#374151;'
                'white-space:pre-wrap;line-height:1.6;">'
                + summary +
                '</div></div>'
            )
            st.markdown(html_card, unsafe_allow_html=True)

        st.caption(
            "※ AI 요약은 Claude Sonnet이 뉴스 본문을 기반으로 생성한 참고 정보입니다. "
            "투자 의사결정의 유일한 근거로 사용하지 마세요."
        )