import hashlib
import json
import os
import re
import feedparser
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_IDS = [cid.strip() for cid in os.environ.get("CHAT_IDS", "").split(",") if cid.strip()]
STATE_FILE = "monitor_state.json"

# Feeds ampliados com canais focados em passagens e superpromoções
FEEDS_RSS = {
    "Passageiro de Primeira": "https://passageirodeprimeira.com/category/promocoes/feed/",
    "Melhores Destinos": "https://www.melhoresdestinos.com.br/feed",
    "Melhores Cartões": "https://www.melhorescartoes.com.br/feed",
    "Pontos pra Voar": "https://pontospravoar.com/feed/",
    "Passagens Imperdíveis": "https://www.passagensimperdiveis.com.br/feed/",
}

PROGRAMAS = [
    "latam pass", "smiles", "tudoazul", "azul fidelidade",
    "livelo", "esfera", "átomos", "c6 átomos", "ultravioleta",
    "tap miles&go", "coopmilhas", "abastece aí", "curtaí"
]

TERMOS_BONUS = [
    "bônus", "transferência", "compre pontos", "desconto na compra",
    "pontos em dobro", "bônus de transferência", "isentaço", "cashback"
]

# Termos focados na origem
TERMOS_ORIGEM_RECIFE = ["recife", "rec", "pe", "nordeste"]

# Termos para detectar menções a passagens normais
TERMOS_PASSAGEM = ["passagens", "voos", "tarifa promocional", "trechos", "ida e volta", "passagem aérea"]

# Termos de quedas históricas e anomalias de preço
TERMOS_QUEDA_HISTORICA = [
    "menor preço", "menor valor histórico", "preço despenca", "queda histórica",
    "muito abaixo do normal", "menor preço do ano", "bug", "erro de tarifa",
    "preço inacreditável", "tarifa imperdível", "superpromoção"
]

def carregar_estado():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def salvar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

def enviar_telegram(mensagem):
    if not BOT_TOKEN or not CHAT_IDS:
        print("Credenciais não configuradas.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": mensagem,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            requests.post(url, json=payload, timeout=12)
        except Exception as e:
            print(f"Erro ao enviar para {chat_id}: {e}")

def extrair_destaque(texto):
    # Destaque de quedas extremas ou erros
    for termo in TERMOS_QUEDA_HISTORICA:
        if termo in texto.lower():
            return f"🚨 <b>QUEDA HISTÓRICA / ANOMALIA:</b> <code>{termo.upper()}</code>\n"

    # Destaque de bônus de milhas
    bonus_match = re.search(r"(\d{2,3}%\s*(?:de\s*)?b[ôo]nus)", texto, re.IGNORECASE)
    if bonus_match:
        return f"🔥 <b>BÔNUS:</b> <code>{bonus_match.group(1).upper()}</code>\n"
    
    # Destaque de tarifas em milhas
    milhas_match = re.search(r"(a\s+partir\s+de\s+[0-9\.]+\s*(?:milhas|pontos))", texto, re.IGNORECASE)
    if milhas_match:
        return f"✈️ <b>TARIFA:</b> <code>{milhas_match.group(1).upper()}</code>\n"
    
    return ""

def filtrar_noticia(titulo, resumo):
    conteudo = f"{titulo} {resumo}".lower()
    
    # 1. Bônus e transferências de programas de fidelidade / cartões (Geral)
    tem_programa = any(p in conteudo for p in PROGRAMAS)
    tem_acao_bonus = any(b in conteudo for b in TERMOS_BONUS)
    if tem_programa and tem_acao_bonus:
        return True

    # 2. Passagens aéreas com origem em Recife
    e_passagem = any(v in conteudo for v in TERMOS_PASSAGEM)
    tem_origem_recife = any(origem in conteudo for origem in TERMOS_ORIGEM_RECIFE)
    if e_passagem and tem_origem_recife:
        return True

    # 3. Bugs ou quedas históricas explícitas com saída de Recife/Nordeste
    tem_queda = any(q in conteudo for q in TERMOS_QUEDA_HISTORICA)
    if tem_queda and tem_origem_recife:
        return True

    return False

def processar_feeds(estado):
    novas_alertas = []
    itens_vistos = set(estado.get("vistos", []))

    for nome_fonte, url_feed in FEEDS_RSS.items():
        try:
            feed = feedparser.parse(url_feed)
            for entry in feed.entries[:10]:
                link = entry.get("link", "")
                titulo = entry.get("title", "")
                resumo = entry.get("summary", "")

                id_item = hashlib.sha256(link.encode("utf-8")).hexdigest()
                if id_item in itens_vistos:
                    continue

                if filtrar_noticia(titulo, resumo):
                    destaque = extrair_destaque(f"{titulo} {resumo}")
                    novas_alertas.append({
                        "fonte": nome_fonte,
                        "titulo": titulo,
                        "link": link,
                        "destaque": destaque
                    })

                itens_vistos.add(id_item)
        except Exception as e:
            print(f"Erro em {nome_fonte}: {e}")

    # Mantém um histórico controlado dos últimos IDs processados
    estado["vistos"] = list(itens_vistos)[-400:]
    return novas_alertas

def monitorar():
    estado = carregar_estado()
    
    if "vistos" not in estado:
        print("Criando estado inicial de controle...")
        processar_feeds(estado)
        salvar_estado(estado)
        print("Estado inicial gravado.")
        return

    alertas = processar_feeds(estado)

    if alertas:
        salvar_estado(estado)
        for item in alertas:
            msg = (
                f"✈️ <b>OPORTUNIDADE DE VIAGEM & MILHAS</b>\n\n"
                f"{item['destaque']}"
                f"📢 <b>{item['titulo']}</b>\n\n"
                f"🌐 <i>Fonte: {item['fonte']}</i>\n"
                f"🔗 <a href='{item['link']}'>Conferir Promoção</a>"
            )
            enviar_telegram(msg)
            print(f"Alerta enviado: {item['titulo']}")
    else:
        salvar_estado(estado)
        print("Nenhuma novidade encontrada.")

if __name__ == "__main__":
    monitorar()
