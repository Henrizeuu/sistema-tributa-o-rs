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
# MOTOR HTTP - LEFISC CLIENT (COM HASH FIXO E PARSER XML)
# ==========================================
class LefiscClient:
    def __init__(self, usuario, senha):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.lefisc.com.br/monitoramentoncm/",
            "Origin": "https://www.lefisc.com.br",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        })
        self.usuario = usuario
        self.senha = senha
        
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
                # O Lefisc retorna um XML, então raspar com BeautifulSoup é a melhor saída
                soup = BeautifulSoup(res.text, 'html.parser')
                
                dados = {
                    'descricao': soup.find('descricao').text if soup.find('descricao') else 'N/A',
                    'piscofins': soup.find('piscofins').text if soup.find('piscofins') else '',
                    'aliquotas': [],
                    'isencoes': [],
                    'beneficios': []
                }
                
                # Coletando Alíquotas normais
                for alq in soup.find_all('cli_aliquotas'):
                    est = alq.find('estado').text if alq.find('estado') else ''
                    icms = alq.find('icms').text if alq.find('icms') else ''
                    notas = alq.find('notas').text if alq.find('notas') else ''
                    dados['aliquotas'].append({'estado': est, 'icms': icms, 'notas': notas})
                
                # Coletando Isenções
                for ise in soup.find_all('cli_notas_isencao'):
                    uf = ise.find('uf').text if ise.find('uf') else ''
                    notas = ise.find('notas').text if ise.find('notas') else ''
                    dados['isencoes'].append({'estado': uf, 'notas': notas})
                    
                # Coletando Benefícios (Reduções)
                for ben in soup.find_all('cli_notas_beneficios'):
                    uf = ben.find('uf').text if ben.find('uf') else ''
                    notas = ben.find('notas').text if ben.find('notas') else ''
                    dados['beneficios'].append({'estado': uf, 'notas': notas})

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
# FUNÇÕES DE APOIO E INTELIGÊNCIA ARTIFICIAL
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

def auditar_com_gemini(produto, ncm, desc_oficial, pis_cofins, icms_completo, cest):
    prompt = f"""
    Você é um sistema extrator de dados tributários estruturados.
    
    CENÁRIO DA EMPRESA:
    - Optante pelo SIMPLES NACIONAL.
    - Vendas EXCLUSIVAMENTE PARA CONSUMIDOR FINAL.
    
    REGRAS DE CLASSIFICAÇÃO (SIGA RIGOROSAMENTE):
    1. ICMS: Ter um código CEST NÃO significa que a operação é sujeita à Substituição Tributária (ST) no Estado. O CEST é apenas uma nomenclatura nacional. 
       -> REGRA DE OURO: Você SÓ PODE classificar como "Substituição Tributária" se as palavras "Substituição", "Retido" ou "ST" estiverem EXPRESSAMENTE ESCRITAS no campo "Texto ICMS". 
       -> Caso o "Texto ICMS" traga apenas uma alíquota (ex: 17%) sem citar ST, classifique OBRIGATORIAMENTE como "Tributação Normal". Se citar Isenção, classifique como "Isenção". NUNCA deduza ST apenas pela presença de um CEST.
    2. PIS_COFINS: Retorne apenas a regra do Simples Nacional citada no texto (Ex: Alíquota Zero, Monofásico, Tributado Normalmente).
    3. DESCRICAO: Responda apenas "sim" ou "nao", verificando se o nome do Produto do Cliente bate com a Descrição Oficial NCM.
    4. CEST: Extraia apenas o código numérico de 7 dígitos da lista fornecida. Se a lista estiver vazia, retorne "N/A".
    
    DADOS DE ENTRADA:
    Produto do Cliente: "{produto}"
    NCM: {ncm}
    Descrição Oficial NCM: "{desc_oficial}"
    Texto PIS/COFINS: {pis_cofins}
    Texto ICMS: {icms_completo}
    Opções de CEST: {cest}
    
    SAÍDA ESPERADA EM JSON PURO:
    {{
        "descricao": "sim",
        "icms": "Tributação Normal",
        "pis_cofins": "Alíquota Zero",
        "cest": "3301301"
    }}
    """
    try:
        response = model.generate_content(prompt)
        texto = response.text.strip()
        
        # Limpa formatações Markdown que a IA possa tentar colocar
        if texto.startswith("```json"):
            texto = texto[7:-3]
        elif texto.startswith("```"):
            texto = texto[3:-3]
            
        return json.loads(texto.strip())
    except Exception as e:
        return {"descricao": "ERRO", "icms": "ERRO", "pis_cofins": "ERRO", "cest": "ERRO"}

# ==========================================
# INTERFACE STREAMLIT E PROCESSAMENTO
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
            
            dados_ncm = lefisc.buscar_dados_ncm(ncm)
            
            if dados_ncm:
                if isinstance(dados_ncm, dict) and "erro" in dados_ncm:
                    st.warning(f"Erro na NCM {ncm}: O Lefisc retornou {dados_ncm['erro']}")
                    continue
                else:
                    # Agora os dados são um dicionário estruturado que montamos com o XML
                    desc_oficial = dados_ncm.get('descricao', 'N/A')
                    html_piscofins = dados_ncm.get('piscofins', '')
                    texto_piscofins = extrair_piscofins(html_piscofins, letra_piscofins)
                    
                    # 1. Puxa Alíquota e Notas
                    icms_str = ""
                    for alq in dados_ncm.get('aliquotas', []):
                        if alq['estado'].upper() == uf_selecionada.upper():
                            icms_str = f"Alíquota: {alq['icms']} | Notas: {alq['notas']}"
                            break
                    
                    # 2. Puxa Isenções (Se houver)
                    isencao_str = ""
                    for ise in dados_ncm.get('isencoes', []):
                        if ise['estado'].upper() == uf_selecionada.upper() and ise['notas']:
                            isencao_str = f"\nIsenção: {ise['notas']}"
                            break
                    
                    # 3. Puxa Benefícios/Reduções (Se houver)
                    beneficio_str = ""
                    for ben in dados_ncm.get('beneficios', []):
                        if ben['estado'].upper() == uf_selecionada.upper() and ben['notas']:
                            beneficio_str = f"\nBenefício/Redução: {ben['notas']}"
                            break
                    
                    # Junta tudo pro Gemini ter o contexto total
                    icms_completo = f"{icms_str}{isencao_str}{beneficio_str}".strip()
                    if not icms_completo:
                        icms_completo = "Não encontrado"
            
            cest_list = lefisc.buscar_cest(ncm)
            
            # Pede a análise estruturada para a IA mandando todo o cenário montado
            parecer_ia = auditar_com_gemini(produto, ncm, desc_oficial, texto_piscofins, icms_completo, cest_list)
            
            # Fatiando a resposta da IA para criar colunas separadas
            res_desc, res_icms, res_pis, res_cest = "N/A", "N/A", "N/A", "N/A"
            
            for linha in parecer_ia.split('\n'):
                if ":" in linha:
                    chave, valor = linha.split(":", 1)
                    chave = chave.strip().upper()
                    valor = valor.strip()
                    if "DESCRICAO" in chave:
                        res_desc = valor
                    elif "ICMS" in chave:
                        res_icms = valor
                    elif "PIS" in chave:
                        res_pis = valor
                    elif "CEST" in chave:
                        res_cest = valor

            # Atribuindo cada dado à sua respectiva coluna
            df.at[i, 'Analise_Descricao'] = res_desc
            df.at[i, 'Tributacao_ICMS'] = res_icms
            df.at[i, 'PIS_COFINS'] = res_pis
            df.at[i, 'Codigo_CEST'] = res_cest
            
            barra_progresso.progress((i + 1) / len(df))
            time.sleep(1) 
            
        status_text.text("Auditoria Concluída!")
        
        # Mantém apenas as colunas essenciais
        colunas_desejadas = ['NCM', 'Descricao_Produto', 'Analise_Descricao', 'Tributacao_ICMS', 'PIS_COFINS', 'Codigo_CEST']
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
