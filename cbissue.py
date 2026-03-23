# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
import pandas as pd
import io
import urllib3
import tempfile
import os

# 關閉 SSL 憑證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 設定區塊 ---
# 從 GitHub Actions 的環境變數 (Secrets) 中讀取 Webhook 網址
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
NOTIFIED_FILE = "notified_cbs.txt"

def load_notified_records():
    """讀取已經通知過的紀錄"""
    if not os.path.exists(NOTIFIED_FILE):
        return set()
    with open(NOTIFIED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def save_notified_record(record_id):
    """將新通知的案件寫入紀錄檔"""
    with open(NOTIFIED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{record_id}\n")

def send_discord_notify(message):
    """發送 Discord 通知"""
    if not DISCORD_WEBHOOK_URL:
        print("尚未設定 Discord Webhook 網址 (環境變數遺失)，略過通知。")
        return
        
    data = {"content": message}
    response = requests.post(DISCORD_WEBHOOK_URL, json=data)
    if response.status_code == 204:
        print("Discord 通知發送成功！")
    else:
        print(f"Discord 通知發送失敗，狀態碼: {response.status_code}")

def get_115_fsc_excel_data():
    """爬取金管會 115 年度申報案件的 Excel 檔案"""
    url = "https://www.sfb.gov.tw/ch/home.jsp?id=1016&parentpath=0,6,52"
    resp = requests.get(url, verify=False)
    soup = BeautifulSoup(resp.text, "html.parser")
    
    tables = soup.find_all("table", {"class": "table01 table02"})
    trs = tables[0].find_all("tr")
    
    # 抓取第 3 列，第 5 欄裡面的 EXCEL 下載連結
    tds = trs[2].find_all("td") 
    file_url = tds[4].find("a").get("href") 
    
    file_resp = requests.get(file_url, verify=False)
    file_resp.raise_for_status()
    
    # 從網址判斷是 .xlsx 還是 .xls，如果都沒有就預設給 .xls
    ext = '.xlsx' if '.xlsx' in file_url.lower() else '.xls'
    
    # 建立暫存檔並寫入內容
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_resp.content)
        tmp_path = tmp.name
        
    try:
        # 讓 pandas 透過真實的暫存檔路徑與副檔名來讀取，指定 header=2
        df = pd.read_excel(tmp_path, header=2)
    finally:
        # 確保讀取完畢或發生錯誤時，都會把暫存檔刪除，不佔用空間
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            
    return df
    
def get_col_name(columns, keyword):
    """輔助函式：用關鍵字找實際的欄位名稱 (模糊比對)"""
    for col in columns:
        if keyword in str(col):
            return col
    return None

if __name__ == "__main__":
    df_data = get_115_fsc_excel_data()
    notified_records = load_notified_records()
    
    # 清理欄位名稱 (去除換行與空白)
    df_data.columns = df_data.columns.astype(str).str.replace('\n', '').str.replace(' ', '')
    
    # 動態抓取實際欄位名稱
    col_target = get_col_name(df_data.columns, '案件類別')
    col_company = get_col_name(df_data.columns, '公司名稱')
    col_code = get_col_name(df_data.columns, '代號')
    col_type = get_col_name(df_data.columns, '型態')
    col_amount = get_col_name(df_data.columns, '金額')
    col_issue_price = get_col_name(df_data.columns, '發行價格') # 新增：抓取發行價格欄位
    col_receipt = get_col_name(df_data.columns, '收文日期')
    col_effective = get_col_name(df_data.columns, '生效日期')
    # 幣別欄位已移除
    
    if col_target:
        # 篩選包含「現金增資」的資料 (忽略空值)
        cb_data = df_data[df_data[col_target].astype(str).str.contains('現金增資', na=False)]
        
        if cb_data.empty:
            print("目前沒有現金增資的案件。")
        
        for index, row in cb_data.iterrows():
            company_name = row[col_company] if col_company else '未知公司'
            case_type = row[col_target] if col_target else '未知案件'
            stock_code = row[col_code] if col_code else '未知'
            company_type = row[col_type] if col_type else '未知'
            receipt_date = row[col_receipt] if col_receipt else '未知'
            effective_date = row[col_effective] if col_effective else '未知'
            
            # --- 處理發行價格 ---
            issue_price_val = row[col_issue_price] if col_issue_price else ''
            if pd.notna(issue_price_val) and str(issue_price_val).strip() != '':
                try:
                    issue_price = f"{float(issue_price_val):.2f}".rstrip('0').rstrip('.')
                except ValueError:
                    issue_price = str(issue_price_val).strip()
            else:
                issue_price = '未訂定'

            # --- 處理金額：轉換為「億」 ---
            amount_val = row[col_amount] if col_amount else ''
            if pd.notna(amount_val) and str(amount_val).strip() != '' and str(amount_val) != '未知':
                try:
                    # 轉成字串並去除千分位逗號，再轉成浮點數計算
                    clean_amount = float(str(amount_val).replace(',', '').strip())
                    
                    # 自動判斷 Excel 欄位名稱是否帶有「仟元」或「萬元」等單位
                    if '仟' in str(col_amount) or '千' in str(col_amount):
                        amount_in_yi = clean_amount / 100000
                    elif '萬' in str(col_amount):
                        amount_in_yi = clean_amount / 10000
                    else:
                        amount_in_yi = clean_amount / 100000000
                    
                    # 格式化輸出，最多顯示小數點後兩位，並去掉結尾多餘的 0 和小數點
                    amount = f"{amount_in_yi:.2f}".rstrip('0').rstrip('.') + " 億"
                    if amount == " 億" or amount == ". 億":
                        amount = "0 億"
                except (ValueError, TypeError):
                    # 如果資料是純文字或異常導致無法計算，就維持原樣
                    amount = str(amount_val)
            else:
                # 金額無資料或未知時，預設顯示 0 億
                amount = '0 億'
            
            # 建立唯一識別碼 (加入收文日期，避免同公司不同次發行被略過)
            record_id = f"{company_name}_{case_type}_{receipt_date}"
            
            if record_id not in notified_records:
                msg = (
                    f"🔔 **新現金增資案件通知** 🔔\n"
                    f"**證券代號**：{stock_code}\n"
                    f"**公司名稱**：{company_name}\n"
                    f"**公司型態**：{company_type}\n"
                    f"**案件類別**：{case_type}\n"
                    f"**金額**：{amount}\n"
                    f"**發行價格**：{issue_price}\n"
                    f"**收文日期**：{receipt_date}\n"
                    f"**生效日期**：{effective_date}\n"
                    f"*(資料來源：金管會證期局)*"
                )
                
                send_discord_notify(msg)
                
                # 紀錄起來，下次就不會再通知
                save_notified_record(record_id)
                notified_records.add(record_id)
            else:
                print(f"[{record_id}] 已通知過，略過。")
    else:
        print("找不到包含 '案件類別' 的欄位。")
        print("目前的欄位有：", df_data.columns.tolist())
