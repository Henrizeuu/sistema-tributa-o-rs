import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import time
import io

# ==========================================
# CONFIGURAÇÕES INICIAIS
# ==========================================
st.set_page_config(page_title="Auditoria NCM - Lefisc", layout="wide")

try:
    genai.configure(api_key=st.secrets["gemini_api_key"])
    model = genai.GenerativeModel('gemini-3.5-flash-lite')
except Exception as e:
    st.error("Erro ao configurar Gemini API. Verifique os Secrets.")

# ==========================================
# MOTOR HTTP - LEFISC CLIENT (COM HASH FIXO)
# ==========================================
class LefiscClient:
    def __init__(self, usuario, senha):
        self.session = requests.Session()
        # Headers idênticos aos do seu navegador
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.lefisc.com.br/monitoramentoncm/",
            "Origin": "https://www.lefisc.com.br",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        })
        self.usuario = usuario
        self.senha = senha
        
        # O Hash fixo mapeado da sua requisição bem-sucedida
        self.hash_monitoramento = "64ad62a25fe48a9e8bcd84df737dd40b"
        
        self.cache_ncm = {}
        self.cache_cest = {}

    def autenticar(self):
        url_login = "https://www.lefisc.com.br/api/validacao/cliente/login"
        payload_multipart = {
            "Usuario": (None, str(self.usuario)),
            "Senha": (None, str(self.senha)),
            "browserId": (None, "d42e01f74d243ddbe00ee3acb9589f9c")
        }
        
        try:
            res = self.session.post(url_login, files=payload_multipart, timeout=10)
            
            if res.status_code == 200:
                dados = res.json()
                token = dados.get("token")
                id_cliente = dados.get("id")
                
                # Injeta os cookies exigidos pela sessão
                self.session.cookies.update({
                    "hash": self.hash_monitoramento,
                    "token": token,
                    "usuario": self.usuario,
                    "login": "Sim",
                    "idCliente": str(id_cliente)
                })
                return True
            else:
                st.error(f"O servidor recusou o login. Código: {res.status_code} | Resposta: {res.text}")
                return False
                
        except Exception as e:
            st.error(f"Erro ao conectar: {e}")
            return False

    def buscar_dados_ncm(self, ncm_limpa):
        if ncm_limpa in self.cache_ncm:
            return self.cache_ncm[ncm_limpa]
            
        ncm_formatada = f"{ncm_limpa[:4]}.{ncm_limpa[4:6]}.{ncm_limpa[6:]}"
        url_ncm = f"https://www.lefisc.com.br/api/monitoramentoNCM/Cliente/dadosNCM/{ncm_formatada}/{self.hash_monitoramento}"
        
        try:
            res = self.session.get(url_ncm, timeout=10)
            if res.status_code == 200:
                dados = res.json()
                self.cache_ncm[ncm_limpa] = dados
                return dados
            else:
                return {"erro": f"Status {res.status_code} | Resposta: {res.text}"}
        except Exception as e:
            return {"erro": str(e)}

    def buscar_cest(self, ncm_limpa):
        if ncm_limpa in self.cache_cest:
            return self.cache_cest[ncm_limpa]
            
        url_detail = "https://www.lefisc.com.br/ncm/Detail.aspx"
        
        try:
            res_get = self.session.get(url_detail, timeout=10)
            soup = BeautifulSoup(res_get.text, 'html.parser')
            
            viewstate = soup.find(id="__VIEWSTATE")['value'] if soup.find(id="__VIEWSTATE") else ""
            viewstategenerator = soup.find(id="__VIEWSTATEGENERATOR")['value'] if soup.find(id="__VIEWSTATEGENERATOR") else ""
            eventvalidation = soup.find(id="__EVENTVALIDATION")['value'] if soup.find(id="__EVENTVALIDATION") else ""
            
            payload = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": viewstate,
                "__VIEWSTATEGENERATOR": viewstategenerator,
                "__EVENTVALIDATION": eventvalidation,
                "tb_busca": ncm_limpa,
                "Button1": "Buscar"
            }
            
            headers_post = {"Content-Type": "application/x-www-form-urlencoded"}
            res_post = self.session.post(url_detail, data=payload, headers=headers_post, timeout=10)
            
            soup_post = BeautifulSoup(res_post.text, 'html.parser')
            comentarios = soup_post.find_all('div', class_='comentario')
            
            textos_cest = []
            for com in comentarios:
                textos_cest.append(com.get_text(separator=" | ", strip=True))
                
            resultado = "\n".join(textos_cest)
            if not resultado:
                resultado = "Nenhum CEST encontrado."
            self.cache_cest[ncm_limpa] = resultado
            return resultado
            
        except Exception as e:
            return f"Erro ao buscar CEST: {e}"

# ==========================================
# FUNÇÕES DE APOIO
# ==========================================
def extrair_piscofins(html_piscofins, opcao_escolhida):
    if not html_piscofins:
        return "PIS/COFINS não encontrado"
        
    soup = BeautifulSoup(html_piscofins, 'html.parser')
    texto_puro = soup.get_text(separator="\n", strip=True)
    
    marcadores = ["A)REGRA GERAL", "B)VENDA PARA PESSOA", "C)VENDA EFETUADA", "D)INDUSTRIALIZAÇÃO"]
    
    idx_inicio = -1
    for m in marcadores:
        if m.startswith(opcao_escolhida + ")"):
            idx_inicio = texto_puro.find(m)
            break
            
    if idx_inicio == -1:
        return "Regra não encontrada"
        
    idx_fim = len(texto_puro)
    for m in marcadores:
        if not m.startswith(opcao_escolhida + ")"):
            idx_temp = texto_puro.find(m, idx_inicio)
            if idx_temp != -1 and idx_temp < idx_fim:
                idx_fim = idx_temp
                
    return texto_puro[idx_inicio:idx_fim].strip()

def auditar_com_gemini(produto, ncm, desc_oficial, pis_cofins, icms, cest):
    prompt = f"""
    Você é um auditor fiscal especialista na legislação tributária brasileira.
    Analise o enquadramento do produto com base nos dados oficiais do Lefisc abaixo:
    
    PRODUTO DO CLIENTE: "{produto}"
    NCM: {ncm}
    Descrição Oficial NCM: "{desc_oficial}"
    
    Tributação Encontrada no Lefisc:
    - PIS/COFINS (Regra do Cenário): {pis_cofins}
    - ICMS (Alíquota do Estado): {icms}
    - Relação de CESTs disponíveis para esta NCM: {cest}
    
    Gere a resposta EXATAMENTE no formato abaixo, sem adicionar introduções ou textos extras:
    
    descrição: [Diga 'sim' se a descrição do produto faz sentido com a NCM, ou 'não' acompanhado de uma justificativa curta]
    icms: [Informe se a tributação é normal, substituição tributária, isenção ou alíquota reduzida com base nos dados]
    pis e cofins: [Informe se é monofásico, alíquota zero, cumulativo, não cumulativo ou tributado normalmente]
    cest: [Escolha e indique o código CEST numérico de 7 dígitos mais adequado dentre a lista do Lefisc para este produto exato]
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Erro na IA: {e}"

# ==========================================
# INTERFACE STREAMLIT
# ==========================================
st.title("⚖️ Auditoria Fiscal Automática - NCM")
st.markdown("Faça o upload da planilha do sistema e cruze com a base oficial do Lefisc.")

st.sidebar.header("Configurações")
uf_selecionada = st.sidebar.selectbox("Estado para ICMS", ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO"], index=25) 

opcao_piscofins = st.sidebar.radio("Cenário PIS/COFINS", [
    "A (Regra Geral - Venda PJ)",
    "B (Venda para Varejista/Consumidor)",
    "C (Venda por Varejista)",
    "D (Industrialização por Encomenda)"
])
letra_piscofins = opcao_piscofins.split(" ")[0]

df_modelo = pd.DataFrame({'NCM': ['22011000', '22021000'], 'Descricao_Produto': ['Agua Mineral 500ml', 'Refrigerante Cola 2L']})
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
    df_modelo.to_excel(writer, index=False, sheet_name='Sheet1')
    
st.download_button(
    label="⬇️ Baixar Planilha Modelo",
    data=buffer.getvalue(),
    file_name="modelo_auditoria.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

st.divider()

arquivo_up = st.file_uploader("Suba a planilha preenchida (.xlsx)", type=["xlsx"])

if arquivo_up is not None:
    df = pd.read_excel(arquivo_up)
    st.write(f"Total de itens para analisar: {len(df)}")
    
    if st.button("🚀 Iniciar Auditoria"):
        try:
            lefisc = LefiscClient(st.secrets["lefisc_user"], st.secrets["lefisc_pass"])
        except Exception:
            st.error("Credenciais do Lefisc não configuradas no Streamlit Secrets.")
            st.stop()
            
        with st.spinner("Autenticando no Lefisc..."):
            if not lefisc.autenticar():
                st.stop()
            st.success("Autenticado com sucesso!")
            
        barra_progresso = st.progress(0)
        status_text = st.empty()
        
         for i, row in df.iterrows():
            produto = str(row.get('Descricao_Produto', ''))
            
            ncm_raw = str(row.get('NCM', ''))
            ncm_raw = ncm_raw.split('.')[0]
            ncm = ''.join(filter(str.isdigit, ncm_raw))
            ncm = ncm.zfill(8)
            
            status_text.text(f"Processando [{i+1}/{len(df)}]: {produto} (NCM Limpa: {ncm})")
            
            desc_oficial = "N/A"
            texto_piscofins = "N/A"
            icms_estado = "N/A"
            cest_list = "N/A"
            
            dados_ncm = lefisc.buscar_dados_ncm(ncm)
            
            if dados_ncm:
                if isinstance(dados_ncm, dict) and "erro" in dados_ncm:
                    st.warning(f"Erro na NCM {ncm}: O Lefisc retornou {dados_ncm['erro']}")
                else:
                    if isinstance(dados_ncm, list) and len(dados_ncm) > 0:
                        item_ncm = dados_ncm[0]
                    elif isinstance(dados_ncm, dict):
                        item_ncm = dados_ncm
                    else:
                        item_ncm = {}

                    desc_oficial = item_ncm.get('descricao', 'N/A')
                    html_piscofins = item_ncm.get('piscofins', '')
                    texto_piscofins = extrair_piscofins(html_piscofins, letra_piscofins)
                    
                    aliquotas = item_ncm.get('aliquotas', [])
                    for alq in aliquotas:
                        if alq.get('estado') == uf_selecionada:
                            icms_estado = alq.get('icms', 'Não encontrado')
                            break
            
            cest_list = lefisc.buscar_cest(ncm)
            
            # Pede a análise estruturada para a IA
            parecer_ia = auditar_com_gemini(produto, ncm, desc_oficial, texto_piscofins, icms_estado, cest_list)
            
            # ATRIBUI APENAS AS RESPOSTAS DIRETAS DA IA NA PLANILHA
            df.at[i, 'Parecer_IA'] = parecer_ia
            
            barra_progresso.progress((i + 1) / len(df))
            time.sleep(1) 
            
        status_text.text("Auditoria Concluída!")
        
        # Mantém apenas as colunas essenciais do sistema + a coluna formatada da IA
        colunas_desejadas = ['NCM', 'Descricao_Produto', 'Parecer_IA']
        # Se houver outras colunas extras na planilha original que você queira manter, pode ajustar aqui
        df_final = df[[col for col in colunas_desejadas if col in df.columns]]

        buffer_final = io.BytesIO()
        with pd.ExcelWriter(buffer_final, engine='openpyxl') as writer:
            df_final.to_excel(writer, index=False)
            
        st.success("Relatório gerado com sucesso!")
        st.download_button(
            label="⬇️ Baixar Resultado da Auditoria Limpo",
            data=buffer_final.getvalue(),
            file_name="resultado_auditoria_limpo.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
