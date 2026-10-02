import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from utils.api import fetch_charging_stations

st.set_page_config(
    page_title="충북 EV 동적 요금 & 관제 플랫폼",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ 충북 도심형 EV 충전소 관제 & 동적 요금 플랫폼")
st.markdown("환경부 공공 표준 데이터를 연동하여 급속/완속 구분 및 혼잡도 기반 동적 요금을 시각화합니다.")
st.markdown("---")

# 1. 사이드바 필터 (충북 전역 시군구 코드 매핑)
st.sidebar.header("🔍 지역 선택")
region_options = {
    "청주시 (43110)": "43110",
    "충주시 (43130)": "43130",
    "제천시 (43150)": "43150",
    "음성군 (43770)": "43770",
    "진천군 (43750)": "43750",
    "증평군 (43745)": "43745",
    "괴산군 (43760)": "43760",
    "단양군 (43800)": "43800"
}
selected_region_name = st.sidebar.selectbox("충북 시·군 선택", list(region_options.keys()))
selected_zscode = region_options[selected_region_name]

# 2. 데이터 수집
@st.cache_data(ttl=600)
def load_data(zcode, zscode):
    return fetch_charging_stations(zcode="43", zscode=zscode, per_page=150)

with st.spinner(f"'{selected_region_name}' 충전기 상세 데이터를 집계 중..."):
    stations = load_data("43", selected_zscode)

if not stations:
    st.warning("선택한 지역의 충전소 데이터가 조회되지 않았습니다. API 키 및 네트워크를 확인하세요.")
else:
    df = pd.DataFrame(stations)
    df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
    df['lng'] = pd.to_numeric(df['lng'], errors='coerce')
    df = df.dropna(subset=['lat', 'lng'])

    # 가이드 문서 chgerType 규격 정의
    slow_type_codes = {'02', '08'}
    
    # 시설구분 코드(kindDetail) 대표 매핑
    kind_map = {
        'A001': '관공서', 'A002': '주민센터', 'B001': '공영주차장', 
        'B004': '일반주차장', 'C001': '휴게소', 'E001': '마트/쇼핑몰', 
        'H001': '아파트', 'I001': '병원', 'E007': '주유소'
    }

    # 3. 충전소(statId) 단위 데이터 그룹화 및 정밀 계산
    grouped_stations = []
    base_rate = 340  # 기준 요금 (원/kWh)

    for stat_id, group in df.groupby('statId'):
        first_row = group.iloc[0]
        total_chger = len(group)
        
        # 가이드 문서 stat 코드 집계
        available_chger = len(group[group['stat'] == '2'])      # 전체 충전대기(사용가능)
        charging_chger = len(group[group['stat'] == '3'])       # 전체 충전중
        error_chger = len(group[group['stat'].isin(['1', '4', '5'])]) # 점검/고장
        
        # 총 급속/완속 대수 및 [핵심] '여유 있는(대기중인)' 급속/완속 대수 집계
        fast_total = 0
        slow_total = 0
        fast_available = 0
        slow_available = 0

        for _, chg in group.iterrows():
            ctype = str(chg.get('chgerType', '')).strip()
            cstat = str(chg.get('stat', '')).strip()
            
            # 완속 판정 ('02': AC완속, '08': DC콤보 완속)
            is_slow = ctype in slow_type_codes
            
            if is_slow:
                slow_total += 1
                if cstat == '2':
                    slow_available += 1
            else:
                fast_total += 1
                if cstat == '2':
                    fast_available += 1

        # 동적 요금제 및 상태 판정 로직
        if available_chger > 0:
            if (available_chger / total_chger) >= 0.5:
                marker_color = 'green'
                rate_status = "혼잡 분산 할인 (-20%)"
                current_price = int(base_rate * 0.8)
            else:
                marker_color = 'orange'
                rate_status = "표준 요금"
                current_price = base_rate
            status_text = f"여유 (급속 {fast_available}/{fast_total}대 | 완속 {slow_available}/{slow_total}대)"
        else:
            if charging_chger > 0:
                marker_color = 'red'
                rate_status = "피크 쏠림 할증 (+15%)"
                current_price = int(base_rate * 1.15)
                status_text = f"만차 ({charging_chger}대 충전중)"
            elif error_chger > 0:
                marker_color = 'gray'
                rate_status = "이용 불가"
                current_price = base_rate
                status_text = f"점검/고장 ({error_chger}대 점검중)"
            else:
                marker_color = 'gray'
                rate_status = "상태 미확인"
                current_price = base_rate
                status_text = "상태 미확인"

        # 주차료 및 시설명 가공
        p_free = "무료" if first_row.get('parkingFree') == 'Y' else "유료/현장확인"
        k_code = first_row.get('kindDetail', '')
        facility_type = kind_map.get(k_code, "일반거점")

        grouped_stations.append({
            'statId': stat_id,
            'statNm': first_row.get('statNm', '이름없음'),
            'addr': f"{first_row.get('addr', '')} {first_row.get('addrDetail', '')}".strip(),
            'lat': first_row['lat'],
            'lng': first_row['lng'],
            'total': total_chger,
            'fast_total': fast_total,
            'slow_total': slow_total,
            'fast_avail': fast_available,
            'slow_avail': slow_available,
            'available': available_chger,
            'status_text': status_text,
            'color': marker_color,
            'price': current_price,
            'rate_status': rate_status,
            'facility': facility_type,
            'parking': p_free,
            'busiNm': first_row.get('busiNm', '운영기관 미표기')
        })

    summary_df = pd.DataFrame(grouped_stations)

    # 4. 레이아웃 분할
    col1, col2 = st.columns([7, 3])

    with col1:
        st.subheader(f"🗺 {selected_region_name} 충전소 위치 및 실시간 혼잡도 지도")
        
        if not summary_df.empty:
            center_lat = summary_df['lat'].mean()
            center_lng = summary_df['lng'].mean()
            
            m = folium.Map(location=[center_lat, center_lng], zoom_start=13, tiles="OpenStreetMap")
            
            for _, row in summary_df.iterrows():
                popup_html = f"""
                <div style="font-family:sans-serif; width:230px; font-size:12px; line-height:1.6;">
                    <b style="font-size:14px; color:#1f2937;">{row['statNm']}</b><br>
                    <span style="color:#6b7280;">구분: {row['facility']} | 주차: {row['parking']}</span>
                    <hr style="margin:6px 0; border:0; border-top:1px solid #e5e7eb;">
                    ⚡ <b>급속 여유:</b> <b style="color:{'#059669' if row['fast_avail']>0 else '#dc2626'};">{row['fast_avail']}</b> / {row['fast_total']}대<br>
                    🔌 <b>완속 여유:</b> <b style="color:{'#059669' if row['slow_avail']>0 else '#dc2626'};">{row['slow_avail']}</b> / {row['slow_total']}대<br>
                    💰 <b>동적 요금:</b> <span style="font-size:13px; font-weight:bold; color:{'#059669' if row['color']=='green' else ('#dc2626' if row['color']=='red' else '#d97706')};">{row['price']}원/kWh</span><br>
                    <span style="font-size:11px; color:#4b5563;">({row['rate_status']})</span>
                </div>
                """
                
                folium.Marker(
                    location=[row['lat'], row['lng']],
                    popup=folium.Popup(popup_html, max_width=260),
                    tooltip=f"{row['statNm']} [{row['price']}원/kWh]",
                    icon=folium.Icon(color=row['color'], icon='bolt', prefix='fa')
                ).add_to(m)
                
            st_folium(m, width="100%", height=430)

        st.subheader("📋 충전소 거점별 상세 및 동적 요금 현황")
        # 'fast', 'slow' 대신 새로 정의한 컬럼명 사용
        view_cols = ['statNm', 'facility', 'fast_avail', 'fast_total', 'slow_avail', 'slow_total', 'price', 'rate_status', 'parking', 'addr']
        display_df = summary_df[view_cols].copy()
        display_df.columns = ['충전소명', '시설구분', '급속여유(대)', '급속전체(대)', '완속여유(대)', '완속전체(대)', '적용단가(원)', '요금정책', '주차료', '주소']
        st.dataframe(display_df, use_container_width=True, height=280)

    with col2:
        st.subheader("📊 지역 인프라 및 전력 부하")
        
        green_k = len(summary_df[summary_df['color'] == 'green'])
        red_k = len(summary_df[summary_df['color'] == 'red'])
        
        m_a, m_b = st.columns(2)
        m_a.metric("🟢 여유 (할인 대상)", f"{green_k}곳")
        m_b.metric("🔴 혼잡 (피크 구간)", f"{red_k}곳")
        
        st.markdown("---")
        st.markdown("⚡ **충전기 타입별 인프라 점유율**")
        # 'fast' -> 'fast_total', 'slow' -> 'slow_total'로 변경
        tot_fast = summary_df['fast_total'].sum()
        tot_slow = summary_df['slow_total'].sum()
        st.bar_chart(pd.Series({'급속 (DC)': tot_fast, '완속 (AC)': tot_slow}))
        
        st.markdown("🏢 **상위 운영기관 분포**")
        top_cpo = summary_df['busiNm'].value_counts().head(4)
        st.bar_chart(top_cpo)
        
        st.markdown("---")
        st.caption("💡 **운영 알고리즘 요약**: 대기 충전기가 절반 이상인 거점에 동적 할인(-20%)을 적용하여 도심 배전망 피크 구간 차량을 인근 유휴 거점으로 유도합니다.")