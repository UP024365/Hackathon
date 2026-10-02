import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from utils.api import fetch_charging_stations

# 1. 페이지 설정 (넓은 화면 레이아웃)
st.set_page_config(
    page_title="충북 EV 동적 요금 & 관제 플랫폼",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ 도심형 전기차 충전소 실시간 관제 대시보드")
st.markdown("공공 API를 연동하여 충청북도 내 실시간 충전소 정보와 상세 잔여 대수를 확인합니다.")
st.markdown("---")

# 2. 사이드바 필터 (지역 선택)
st.sidebar.header("🔍 검색 필터")
region_options = {
    "청주시 (43110)": "43110",
    "충주시 (43130)": "43130",
    "제천시 (43150)": "43150",
    "음성군 (43770)": "43770"
}
selected_region_name = st.sidebar.selectbox("시군구 선택", list(region_options.keys()))
selected_zscode = region_options[selected_region_name]

# 3. 데이터 가져오기 (캐싱 적용)
@st.cache_data(ttl=600)
def load_data(zcode, zscode):
    raw_data = fetch_charging_stations(zcode="43", zscode=zscode, per_page=100)
    return raw_data

with st.spinner(f"'{selected_region_name}' 충전소 정보를 불러오는 중..."):
    stations = load_data("43", selected_zscode)

# 4. 화면 구성 및 충전소별 그룹화 (핵심 로직)
if not stations:
    st.warning("조회된 충전소 데이터가 없습니다. API 키나 네트워크 상태를 확인해주세요.")
else:
    df = pd.DataFrame(stations)
    
    # 위경도 숫자 변환
    df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
    df['lng'] = pd.to_numeric(df['lng'], errors='coerce')
    df = df.dropna(subset=['lat', 'lng'])

    # [핵심] 동일한 충전소(statId)별로 충전기 개수 및 가용 대수 집계
    grouped_stations = []
    
    for stat_id, group in df.groupby('statId'):
        first_row = group.iloc[0]
        total_chger = len(group)
        
        # 상태가 '2'(충전대기)인 충전기 대수 계산
        available_chger = len(group[group['stat'] == '2'])
        charging_now = len(group[group['stat'] == '3'])
        
        # 혼잡도 판정 (여유 대수가 없거나 충전 중인 비율이 높으면 혼잡)
        if available_chger > 0:
            status_text = f"충전대기 ({available_chger}/{total_chger}대 여유)"
            marker_color = 'green'
        else:
            status_text = f"만차/혼잡 (0/{total_chger}대 여유)"
            marker_color = 'red'
            
        grouped_stations.append({
            'statId': stat_id,
            'statNm': first_row.get('statNm'),
            'addr': first_row.get('addr'),
            'lat': first_row['lat'],
            'lng': first_row['lng'],
            'total': total_chger,
            'available': available_chger,
            'status_text': status_text,
            'color': marker_color,
            'useTime': first_row.get('useTime', '24시간'),
            'busiNm': first_row.get('busiNm', '정보없음')
        })

    summary_df = pd.DataFrame(grouped_stations)

    st.success(f"총 {len(summary_df)}개의 충전소 거점이 집계되었습니다!")

    # 5. 레이아웃 분할 (좌측: 지도 및 표 / 우측: 상태 요약)
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader(f"🗺 {selected_region_name} 충전소 혼잡도 지도")
        
        if not summary_df.empty:
            center_lat = summary_df['lat'].iloc[0]
            center_lng = summary_df['lng'].iloc[0]
            
            m = folium.Map(location=[center_lat, center_lng], zoom_start=12, tiles="OpenStreetMap")
            
            for _, row in summary_df.iterrows():
                # 팝업에 잔여 대수 상세히 노출
                popup_html = f"""
                <div style="width:220px;">
                    <b>{row['statNm']}</b><br>
                    📍 {row['addr']}<br>
                    ⚡ 전체 충전기: <b>{row['total']}대</b><br>
                    🟢 이용 가능: <b style="color:green;">{row['available']}대</b><br>
                    상태: <b>{row['status_text']}</b>
                </div>
                """
                
                folium.Marker(
                    location=[row['lat'], row['lng']],
                    popup=folium.Popup(popup_html, max_width=250),
                    tooltip=f"{row['statNm']} (여유: {row['available']}대)",
                    icon=folium.Icon(color=row['color'], icon='bolt', prefix='fa')
                ).add_to(m)
                
            st_folium(m, width="100%", height=400)
        else:
            st.info("지도에 표시할 좌표 정보가 없습니다.")
            
        st.markdown("---")
        st.subheader(f"📋 {selected_region_name} 충전소 거점별 상세 현황")
        
        # 표에 보여줄 컬럼 정리
        view_table = summary_df[['statNm', 'addr', 'total', 'available', 'status_text', 'busiNm']].copy()
        view_table.columns = ['충전소명', '주소', '총 충전기', '이용 가능(대기)', '현재 상태', '운영기관']
        st.dataframe(view_table, use_container_width=True, height=350)
        
    with col2:
        st.subheader("📊 실시간 거점 요약")
        if not summary_df.empty:
            color_counts = summary_df['color'].value_counts()
            # 간단한 지표 카드 표시
            green_count = len(summary_df[summary_df['color'] == 'green'])
            red_count = len(summary_df[summary_df['color'] == 'red'])
            
            st.metric(label="🟢 여유 있는 충전소 거점", value=f"{green_count}곳")
            st.metric(label="🔴 혼잡/만차 충전소 거점", value=f"{red_count}곳")
        
        st.markdown("---")
        st.info(
            "💡 **업데이트 포인트**\n\n"
            "- 각 충전소별로 설치된 **총 충전기 수**와 **지금 바로 쓸 수 있는 대기 수**를 계산하여 마커 팝업에 띄우도록 개선했습니다.\n"
            "- 시연할 때 마커를 클릭하면 **'2대 중 1대 여유'** 같은 디테일한 정보를 보여줄 수 있습니다!"
        )