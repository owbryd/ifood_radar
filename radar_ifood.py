import sys
import os
os.environ["PYTHONUTF8"] = "1"

import asyncio
import json
import time
import os
import requests
from datetime import datetime
from collections import defaultdict

import nodriver as uc


async def safe_stop(browser):
    try:
        await browser.send(uc.cdp.browser.close())
    except Exception:
        pass

    for _ in range(20):
        if getattr(browser, "stopped", False):
            break
        await asyncio.sleep(0.25)
    else:
        await asyncio.sleep(1)

    try:
        result = browser.stop()
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"Aviso ao fechar o navegador: {e}")


async def capture_tokens():
    print("Abrindo Chrome...")
    print("(Se pedir login, faça login normalmente no navegador)")
    print()


    profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile_ifood")
    os.makedirs(profile_dir, exist_ok=True)

    browser = await uc.start(
        headless=False,
        lang="pt-BR",
        user_data_dir=profile_dir,
    )

    bearer_token = None
    captured_headers = {}

    page = await browser.get("about:blank")
    await page.send(uc.cdp.network.enable())

    async def on_request(event: uc.cdp.network.RequestWillBeSent):
        nonlocal bearer_token, captured_headers
        url = event.request.url
        if "site-api/v4/customers/me/orders" in url:
            headers = event.request.headers
            auth = None
            for key in headers:
                if key.lower() == "authorization":
                    auth = headers[key]
                    break
            if auth and auth.startswith("Bearer "):
                bearer_token = auth
                captured_headers = dict(headers)
                print(f"Bearer token capturado!")

    page.add_handler(uc.cdp.network.RequestWillBeSent, on_request)

    print("Navegando para iFood pedidos...")
    await page.get("https://www.ifood.com.br/pedidos")



    for i in range(120):
        if bearer_token:
            break
        await asyncio.sleep(1)
        if (i + 1) % 10 == 0:
            print(f"...aguardando ({i+1}s)")

    if not bearer_token:
        print("Não foi possível capturar o Bearer.")
        await safe_stop(browser)
        return None, None, None

    print("Capturando cookies...")
    cookies_response = await page.send(uc.cdp.network.get_cookies())
    
    cf_clearance = None
    all_cookies = {}
    for cookie in cookies_response:
        all_cookies[cookie.name] = cookie.value
        if cookie.name == "cf_clearance":
            cf_clearance = cookie.value
            print(f"cf_clearance capturado!")

    if not cf_clearance:
        print("cf_clearance não encontrado (pode não ser necessário)")

    print("Fechando navegador...")
    await safe_stop(browser)

    return bearer_token, cf_clearance, captured_headers


def get_request_headers(bearer_token, cf_clearance, captured_headers=None):
    headers = {
        "Authorization": bearer_token,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "accept-language": "pt-BR,pt;q=1",
        "app_name": "consumer_webapp",
        "app_version": "9.171.5",
        "browser": "Windows",
        "Cache-Control": "no-cache, no-store",
        "platform": "Desktop",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "x-device-model": "Windows Chrome",
        "Referer": "https://www.ifood.com.br/pedidos",
        "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if cf_clearance:
        headers["Cookie"] = f"cf_clearance={cf_clearance}"
    return headers


def fetch_all_orders(bearer_token, cf_clearance, captured_headers=None, page_size=100):
    headers = get_request_headers(bearer_token, cf_clearance, captured_headers)
    base_url = "https://www.ifood.com.br/site-api/v4/customers/me/orders"

    all_orders = []
    seen_ids = set()
    page = 0
    max_pages = 500  # max de paginas

    while page < max_pages:
        url = f"{base_url}?page={page}&size={page_size}"
        print(f"Página {page}...", end=" ", flush=True)

        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"\nErro de conexão: {e}")
            break

        if response.status_code == 403:
            print(f"\nErro 403.")
            break
        elif response.status_code == 401:
            print(f"\nErro 401")
            break
        elif response.status_code != 200:
            print(f"\nErro {response.status_code}")
            break

        try:
            orders = response.json()
        except json.JSONDecodeError:
            print(f"\nResposta não é JSON")
            break

        if not orders or not isinstance(orders, list) or len(orders) == 0:
            print("(vazia - fim)")
            break


        page_ids = [o.get("id") for o in orders if o.get("id")]
        novos = [oid for oid in page_ids if oid not in seen_ids]

        if page_ids and not novos:
            print("(repetida - fim)")
            break

        seen_ids.update(page_ids)
        all_orders.extend(orders)
        print(f"({len(orders)} pedidos)")


        page += 1
        time.sleep(0.3)
    else:
        print(f"\nAtingido o limite de segurança de {max_pages} páginas.")

    return all_orders


def format_brl(centavos):
    """Formata centavos para R$ X.XXX,XX"""
    reais = centavos / 100
    inteiro = int(reais)
    decimal = round((reais - inteiro) * 100)
    inteiro_fmt = f"{inteiro:,}".replace(",", ".")
    return f"R$ {inteiro_fmt},{decimal:02d}"


def generate_dashboard_html(ctx):

    type_labels = {
        "RESTAURANT": "Restaurantes",
        "PHARMACY": "Farmácias",
        "BEVERAGE": "Bebidas",
        "GROCERY": "Mercado",
        "MARKET": "Mercado",
        "PET": "Pet",
    }

    meses_pt = {
        "01": "Jan", "02": "Fev", "03": "Mar", "04": "Abr", "05": "Mai", "06": "Jun",
        "07": "Jul", "08": "Ago", "09": "Set", "10": "Out", "11": "Nov", "12": "Dez",
    }

    month_labels = [f"{meses_pt.get(m.split('-')[1], m)}/{m.split('-')[0][2:]}" for m in ctx["gastos_por_mes"].keys()]
    month_values = [round(v / 100, 2) for v in ctx["gastos_por_mes"].values()]
    max_month = max(month_values, default=1) or 1

    tipo_items = sorted(ctx["gastos_por_tipo_merchant"].items(), key=lambda x: -x[1])
    tipo_labels = [type_labels.get(t, t) for t, _ in tipo_items]
    tipo_values = [round(v / 100, 2) for _, v in tipo_items]
    tipo_total = sum(tipo_values) or 1
    tipo_colors = ['#3b82f6', '#f0b429', '#941ac7', '#4ade80', '#7c8cff', '#e879f9']

    merchants_sorted = sorted(ctx["gastos_por_merchant"].items(), key=lambda x: -x[1]["total"])[:12]
    metodos_sorted = sorted(ctx["gastos_por_metodo"].items(), key=lambda x: -x[1])

    max_merchant = max((d["total"] for _, d in merchants_sorted), default=1) or 1
    max_metodo = max((v for _, v in metodos_sorted), default=1) or 1

    def brl(centavos):
        return format_brl(centavos)

    merchants_html = "\n".join(f"""
        <div class="receipt-line">
          <div class="receipt-line-top">
            <span class="rl-name">{name}</span>
            <span class="rl-value">{brl(data['total'])}</span>
          </div>
          <div class="rl-bar-track"><div class="rl-bar" style="width:{round(data['total']/max_merchant*100,1)}%"></div></div>
          <span class="rl-count">{data['count']} pedido{'s' if data['count'] != 1 else ''}</span>
        </div>""" for name, data in merchants_sorted)

    metodos_html = "\n".join(f"""
        <div class="receipt-line">
          <div class="receipt-line-top">
            <span class="rl-name">{metodo}</span>
            <span class="rl-value">{brl(valor)}</span>
          </div>
          <div class="rl-bar-track"><div class="rl-bar rl-bar-gold" style="width:{round(valor/max_metodo*100,1)}%"></div></div>
        </div>""" for metodo, valor in metodos_sorted)

    top_entregadores = ctx.get("top_entregadores") or []
    max_entregas = max((d["count"] for d in top_entregadores), default=1) or 1
    entregadores_html = "\n".join(f"""
        <div class="driver-line">
          <div class="driver-avatar-wrap">
            <div class="driver-avatar-fallback">{(d['nome'] or '?')[:1].upper()}</div>
          </div>
          <div class="driver-info">
            <div class="driver-line-top">
              <span class="rl-name">{d['nome']}</span>
              <span class="rl-value">{d['count']} entrega{'s' if d['count'] != 1 else ''}</span>
            </div>
            <div class="rl-bar-track"><div class="rl-bar rl-bar-driver" style="width:{round(d['count']/max_entregas*100,1)}%"></div></div>
          </div>
        </div>""" for d in top_entregadores)
    entregadores_section = "" if not top_entregadores else f"""
    <section class="card">
      <h2>Top entregadores</h2>
      <div class="driver-list">
        {entregadores_html}
      </div>
    </section>"""

    refunds = ctx["pedidos_com_reembolso"]
    refunds_html = "" if not refunds else f"""
      <section class="card">
        <h2>Reembolsos <span class="muted">({len(refunds)})</span></h2>
        <div class="refund-list">
          {''.join(f'<div class="refund-item"><span>{r["merchant"]}</span><span class="refund-value">-{brl(r["valor"])}</span></div>' for r in refunds)}
        </div>
      </section>"""

    pedido_top = ctx.get("pedido_mais_caro")
    if not pedido_top:
        pedido_mais_caro_html = ""
    else:
        itens_html = "\n".join(f"""
            <div class="item-line">
              <div class="item-line-top">
                <span class="item-name">{it['quantidade']}x {it['nome']}</span>
                <span class="item-price">{brl(it['preco_total'])}</span>
              </div>
              {'<div class="item-note">obs: ' + it['observacoes'] + '</div>' if it.get('observacoes') else ''}
              {"".join(f'<div class="subitem-line">- {sub["quantidade"]}x {sub["nome"]}' + (f' <span class="muted">(+{brl(sub["preco"])})</span>' if sub["preco"] else '') + '</div>' for sub in it.get('subitens', []))}
            </div>""" for it in pedido_top["itens"])

        extra_stats = ""
        if pedido_top["valor_bruto"] != pedido_top["valor_liquido"]:
            extra_stats += f"""
              <div class="top-stat"><span class="muted">Valor bruto cobrado</span><span>{brl(pedido_top['valor_bruto'])}</span></div>"""
        if pedido_top["delivery_fee"]:
            extra_stats += f"""
              <div class="top-stat"><span class="muted">Taxa de entrega</span><span>{brl(pedido_top['delivery_fee'])}</span></div>"""

        pedido_mais_caro_html = f"""
      <section class="card top-order-card" style="margin-top:20px">
        <h2>O PEDIDO MAIS CARO</h2>
        <div class="top-order-header">
          <div>
            <div class="top-order-merchant">{pedido_top['merchant']}</div>
            <div class="muted" style="font-size:12px">Pedido #{pedido_top['order_number']} · {pedido_top['data']}</div>
          </div>
          <div class="top-order-value">{brl(pedido_top['valor_liquido'])}</div>
        </div>
        {f'<div class="top-stats">{extra_stats}</div>' if extra_stats else ''}
        <div class="item-list">
          {itens_html if itens_html else '<div class="muted">Nenhum produto detalhado encontrado.</div>'}
        </div>
      </section>"""

    bar_cols_html = "\n".join(f"""
        <div class="bar-col">
          <span class="bar-value">R$ {v:,.0f}</span>
          <div class="bar-track"><div class="bar-fill" style="height:{round(v/max_month*100,1)}%"></div></div>
          <span class="bar-label">{lbl}</span>
        </div>""".replace(",", ".") for lbl, v in zip(month_labels, month_values))

    stops = []
    acc = 0.0
    for i, v in enumerate(tipo_values):
        pct = v / tipo_total * 100
        color = tipo_colors[i % len(tipo_colors)]
        stops.append(f"{color} {acc:.2f}% {acc+pct:.2f}%")
        acc += pct
    conic = ", ".join(stops) if stops else "#2c221c 0% 100%"
    legend_html = "\n".join(
        f'<div class="legend-item"><span class="legend-dot" style="background:{tipo_colors[i % len(tipo_colors)]}"></span>{lbl} <span class="legend-value">{v:,.0f}%</span></div>'.replace(",", ".")
        for i, (lbl, v) in enumerate(zip(tipo_labels, [round(v/tipo_total*100,1) for v in tipo_values]))
    )

    orders_rows = "\n".join(f"""
        <tr>
          <td class="mono">{p['date'][:10] if p['date'] else '—'}</td>
          <td>{p['merchant']}</td>
          <td class="muted">{type_labels.get(p['type'], p['type'])}</td>
          <td class="mono right">{brl(p['value'])}</td>
        </tr>""" for p in ctx["pedidos_detalhados"])

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard de Gastos — iFood</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #15100d;
    --card: #1e1713;
    --card-2: #241c17;
    --border: #2c221c;
    --accent: #ff4632;
    --gold: #f0b429;
    --text: #f5eee4;
    --muted: #9c8f82;
    --green: #4ade80;
    --pink:  #ec0edb;
    --green2: #b6d7a8;
    --yellow: #fff900;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    background-image: radial-gradient(circle at 10% 0%, rgba(255,70,50,0.06), transparent 40%);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    padding: 32px 20px 80px;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  .eyebrow {{
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: .18em;
    text-transform: uppercase;
    font-size: 12px;
    color: var(--accent);
    margin-bottom: 8px;
  }}
  header.receipt {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 36px 40px 28px;
    margin-bottom: 28px;
    position: relative;
    overflow: hidden;
  }}
  header.receipt::after {{
    content: "";
    position: absolute;
    left: 0; right: 0; bottom: 0;
    height: 10px;
    background: radial-gradient(circle at 8px 0, transparent 6px, var(--bg) 6.5px) 0 -5px / 16px 10px repeat-x;
  }}
  h1.total {{
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: clamp(40px, 7vw, 64px);
    margin: 0 0 4px;
    line-height: 1;
  }}
  .total-sub {{
    font-family: 'IBM Plex Mono', monospace;
    color: var(--muted);
    font-size: 13px;
  }}
  .stat-strip {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-top: 24px;
    padding-top: 20px;
    border-top: 1px dashed var(--border);
  }}
  .stat {{ display: flex; flex-direction: column; gap: 4px; }}
  .stat-label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--muted);
  }}
  .stat-value {{
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 600;
    font-size: 19px;
  }}
  .grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 20px;
    margin-bottom: 20px;
  }}
  .grid.grid-3 {{
    grid-template-columns: 1fr 1fr 1fr;
  }}
  @media (max-width: 760px) {{
    .grid, .grid.grid-3, .stat-strip {{ grid-template-columns: 1fr; }}
    header.receipt {{ padding: 28px 22px 22px; }}
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 24px 26px;
  }}
  .card h2 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 15px;
    text-transform: uppercase;
    letter-spacing: .06em;
    margin: 0 0 18px;
  }}
  .card h2 .muted {{ color: var(--muted); font-weight: 400; text-transform: none; letter-spacing: normal; }}
  .receipt-line {{ margin-bottom: 14px; }}
  .receipt-line:last-child {{ margin-bottom: 0; }}
  .receipt-line-top {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
  .rl-name {{ font-size: 14px; }}
  .rl-value {{ font-family: 'IBM Plex Mono', monospace; font-size: 14px; color: var(--text); white-space: nowrap; }}
  .rl-bar-track {{ height: 4px; background: var(--card-2); border-radius: 2px; margin-top: 6px; overflow: hidden; }}
  .rl-bar {{ height: 100%; background: var(--green2); border-radius: 2px; }}
  .rl-bar-gold {{ background: var(--yellow); }}
  .rl-count {{ font-size: 11px; color: var(--muted); }}
  .refund-list {{ display: flex; flex-direction: column; gap: 10px; }}
  .refund-item {{ display: flex; justify-content: space-between; font-size: 14px; border-bottom: 1px dashed var(--border); padding-bottom: 8px; }}
  .refund-value {{ font-family: 'IBM Plex Mono', monospace; color: var(--green); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{
    text-align: left;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--muted);
    padding: 8px 6px;
    border-bottom: 1px solid var(--border);
  }}
  td {{ padding: 9px 6px; border-bottom: 1px solid var(--card-2); }}
  td.right {{ text-align: right; }}
  td.mono {{ font-family: 'IBM Plex Mono', monospace; }}
  .muted {{ color: var(--muted); }}
  .table-wrap {{ max-height: 420px; overflow-y: auto; }}
  .bar-chart {{ display: flex; align-items: flex-end; gap: 10px; height: 200px; padding-top: 10px; }}
  .bar-col {{ flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }}
  .bar-value {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--muted); margin-bottom: 6px; }}
  .bar-track {{ width: 100%; max-width: 40px; flex: 1; display: flex; align-items: flex-end; background: var(--card-2); border-radius: 3px 3px 0 0; overflow: hidden; }}
  .bar-fill {{ width: 100%; background: linear-gradient(180deg, var(--accent), #b8281a); border-radius: 3px 3px 0 0; min-height: 2px; }}
  .bar-label {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--muted); margin-top: 8px; }}
  .donut-wrap {{ display: flex; flex-direction: column; align-items: center; gap: 20px; }}
  .donut {{
    width: 160px; height: 160px; border-radius: 50%;
    background: conic-gradient({conic});
    position: relative;
    flex-shrink: 0;
  }}
  .donut::after {{
    content: "";
    position: absolute; inset: 24px;
    background: var(--card);
    border-radius: 50%;
  }}
  .legend {{ display: flex; flex-direction: column; gap: 8px; width: 100%; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; font-size: 13px; }}
  .legend-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
  .legend-value {{ margin-left: auto; font-family: 'IBM Plex Mono', monospace; color: var(--muted); font-size: 12px; }}
  .top-order-card {{ border-color: var(--gold); }}
  .top-order-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    padding-bottom: 16px;
    margin-bottom: 16px;
    border-bottom: 1px dashed var(--border);
  }}
  .top-order-merchant {{ font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 18px; }}
  .top-order-value {{
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 600;
    font-size: 22px;
    color: var(--gold);
    white-space: nowrap;
  }}
  .top-stats {{ display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }}
  .top-stat {{ display: flex; justify-content: space-between; font-size: 13px; }}
  .item-list {{ display: flex; flex-direction: column; gap: 12px; }}
  .item-line {{ border-bottom: 1px solid var(--card-2); padding-bottom: 10px; }}
  .item-line:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .item-line-top {{ display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }}
  .item-price {{ font-family: 'IBM Plex Mono', monospace; color: var(--accent); white-space: nowrap; }}
  .item-note {{ font-size: 12px; color: var(--muted); margin-top: 2px; font-style: italic; }}
  .subitem-line {{ font-size: 12px; color: var(--muted); margin-top: 3px; margin-left: 12px; }}
  .driver-list {{ display: flex; flex-direction: column; gap: 14px; }}
  .driver-line {{ display: flex; align-items: center; gap: 12px; }}
  .driver-avatar-wrap {{ width: 38px; height: 38px; flex-shrink: 0; }}
  .driver-avatar-fallback {{
    width: 38px; height: 38px; border-radius: 50%;
    background: var(--card-2); border: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center;
    font-family: 'Space Grotesk', sans-serif; font-weight: 700;
    color: var(--muted); font-size: 15px;
  }}
  .driver-info {{ flex: 1; min-width: 0; }}
  .driver-line-top {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
  .rl-bar-driver {{ background: var(--pink); }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 30px; font-family: 'IBM Plex Mono', monospace; }}
</style>
</head>
<body>
<div class="wrap">

  <header class="receipt">
    <div class="eyebrow">Resumo de gastos · iFood</div>
    <h1 class="total">{brl(ctx['total_gasto'])}</h1>
    <div class="total-sub">gasto líquido em {ctx['pedidos_considerados']} pedidos concluídos</div>
    <div class="stat-strip">
      <div class="stat">
        <span class="stat-label">Ticket médio</span>
        <span class="stat-value">{brl(ctx['total_gasto'] // ctx['pedidos_considerados']) if ctx['pedidos_considerados'] else '—'}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Taxas de entrega</span>
        <span class="stat-value">{brl(ctx['total_delivery_fee'])}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Taxas de serviço</span>
        <span class="stat-value">{brl(ctx['total_service_fee'])}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Descontos ganhos</span>
        <span class="stat-value" style="color:var(--green)">{brl(ctx['total_descontos'])}</span>
      </div>
    </div>
  </header>

  <div class="grid">
    <section class="card">
      <h2>Gasto por mês</h2>
      <div class="bar-chart">
        {bar_cols_html}
      </div>
    </section>
    <section class="card">
      <h2>Por categoria</h2>
      <div class="donut-wrap">
        <div class="donut"></div>
        <div class="legend">
          {legend_html}
        </div>
      </div>
    </section>
  </div>

  <div class="grid grid-3">
    <section class="card">
      <h2>Top estabelecimentos</h2>
      {merchants_html}
    </section>
    <section class="card">
      <h2>Métodos de pagamento</h2>
      {metodos_html}
    </section>
    {entregadores_section}
  </div>

  {refunds_html}

  {pedido_mais_caro_html}

  <section class="card" style="margin-top:20px">
    <h2>Todos os pedidos <span class="muted">({len(ctx['pedidos_detalhados'])})</span></h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Data</th><th>Estabelecimento</th><th>Categoria</th><th style="text-align:right">Valor</th></tr>
        </thead>
        <tbody>
          {orders_rows}
        </tbody>
      </table>
    </div>
  </section>

  <footer>gerado automaticamente pelo ifood_gastos.py</footer>
</div>
</body>
</html>"""
    return html


def montar_dados_pedido_mais_caro(order, valor_liquido):
    """Extrai do pedido bruto os dados necessários para exibir no dashboard
    (estabelecimento, data, valores e produtos), em formato simples/serializável."""
    if order is None:
        return None

    merchant = order.get("merchant", {}).get("name", "Desconhecido")
    created_at = order.get("createdAt", "")
    order_number = order.get("orderNumber", order.get("shortId", ""))

    data_fmt = created_at
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            data_fmt = dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass

    payment_total = order.get("payments", {}).get("total", {}).get("value", 0)
    delivery_fee = order.get("bag", {}).get("deliveryFee", {}).get("valueWithDiscount", 0)

    itens = []
    for item in order.get("bag", {}).get("items", []):
        subitens = [
            {
                "nome": sub.get("name", ""),
                "quantidade": sub.get("quantity", 1),
                "preco": sub.get("totalPriceWithDiscount", sub.get("totalPrice", 0)),
            }
            for sub in (item.get("subItems") or [])
        ]
        itens.append({
            "nome": item.get("name", "Item"),
            "quantidade": item.get("quantity", 1),
            "preco_total": item.get("totalPriceWithDiscount", item.get("totalPrice", 0)),
            "preco_unitario": item.get("unitPriceWithDiscount", item.get("unitPrice", 0)),
            "observacoes": item.get("notes"),
            "subitens": subitens,
        })

    return {
        "merchant": merchant,
        "order_number": order_number,
        "data": data_fmt,
        "valor_liquido": valor_liquido,
        "valor_bruto": payment_total,
        "delivery_fee": delivery_fee,
        "itens": itens,
    }


def format_brl_valor(valor_centavos):
    """Formata um valor em centavos para BRL, usando a função format_brl já existente."""
    return format_brl(valor_centavos)


def imprimir_detalhes_pedido(order, valor_liquido):
    """Imprime os detalhes completos de um pedido, incluindo os produtos comprados."""
    merchant = order.get("merchant", {}).get("name", "Desconhecido")
    created_at = order.get("createdAt", "")
    order_number = order.get("orderNumber", order.get("shortId", ""))

    data_fmt = created_at
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            data_fmt = dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass

    print(f"Estabelecimento: {merchant}")
    print(f"Número do pedido: {order_number}")
    print(f"Data: {data_fmt}")
    print(f"Valor líquido pago: {format_brl_valor(valor_liquido)}")

    payment_total = order.get("payments", {}).get("total", {}).get("value", 0)
    if payment_total != valor_liquido:
        print(f"Valor bruto cobrado: {format_brl_valor(payment_total)}")

    delivery_fee = order.get("bag", {}).get("deliveryFee", {}).get("valueWithDiscount", 0)
    if delivery_fee:
        print(f"Taxa de entrega: {format_brl_valor(delivery_fee)}")

    items = order.get("bag", {}).get("items", [])
    if items:
        print(f"\nProdutos ({len(items)} {'item' if len(items) == 1 else 'itens'}):")
        for item in items:
            nome = item.get("name", "Item")
            qtd = item.get("quantity", 1)
            preco_total = item.get("totalPriceWithDiscount", item.get("totalPrice", 0))
            preco_unit = item.get("unitPriceWithDiscount", item.get("unitPrice", 0))
            linha = f"      • {qtd}x {nome} — {format_brl_valor(preco_total)}"
            if qtd and qtd > 1 and preco_unit:
                linha += f" ({format_brl_valor(preco_unit)}/un)"
            print(linha)

            observacoes = item.get("notes")
            if observacoes:
                print(f"obs: {observacoes}")

            for sub in item.get("subItems", []) or []:
                sub_nome = sub.get("name", "")
                sub_qtd = sub.get("quantity", 1)
                sub_preco = sub.get("totalPriceWithDiscount", sub.get("totalPrice", 0))
                extra = f" (+{format_brl_valor(sub_preco)})" if sub_preco else ""
                print(f"- {sub_qtd}x {sub_nome}{extra}")
    else:
        print("\nNenhum produto detalhado encontrado nesse pedido.")


def analyze_orders(orders):
    """Analisa e exibe o relatório completo."""
    if not orders:
        print("\nNenhum pedido encontrado.")
        return

    total_gasto = 0
    total_bruto = 0
    total_reembolsado = 0
    total_delivery_fee = 0
    total_service_fee = 0
    total_descontos = 0
    gastos_por_mes = defaultdict(int)
    gastos_por_ano = defaultdict(int)
    gastos_por_merchant = defaultdict(lambda: {"total": 0, "count": 0})
    gastos_por_tipo_merchant = defaultdict(int)
    gastos_por_metodo = defaultdict(int)
    entregas_por_entregador = defaultdict(lambda: {"nome": None, "count": 0})
    status_count = defaultdict(int)
    pedidos_com_reembolso = []
    pedidos_detalhados = []

    pedidos_considerados = 0

    pedido_mais_caro = None
    valor_pedido_mais_caro = -1

    for order in orders:
        status = order.get("lastStatus", "UNKNOWN")
        status_count[status] += 1
        if status != "CONCLUDED":
            continue

        pedidos_considerados += 1

        payment_total = order.get("payments", {}).get("total", {}).get("value", 0)
        total_bruto += payment_total


        refund_info = order.get("refund")
        refund_centavos = 0

        hc_bag = order.get("historyChanges", {}).get("bag", {})
        hc_ac = hc_bag.get("amountChange", {})
        hc_refund_centavos = hc_ac.get("value", 0) if hc_ac.get("type") == "REFUND" else 0

        if refund_info:
            refund_reais = refund_info.get("paymentRefundAmount", 0) or 0
            refund_centavos = round(refund_reais * 100)

            if refund_centavos > 0:
                methods_sum = sum(
                    m.get("amount", {}).get("value", 0)
                    for m in order.get("payments", {}).get("methods", [])
                )
                pt_already_adjusted = (
                    hc_refund_centavos > 0
                    and (payment_total + refund_centavos) == methods_sum
                )

                if pt_already_adjusted:
                    total_reembolsado += refund_centavos
                    pedidos_com_reembolso.append({
                        "merchant": order.get("merchant", {}).get("name", "Desconhecido"),
                        "tipo": refund_info.get("type", "REFUND") + " (itens removidos)",
                        "valor": refund_centavos,
                    })
                    refund_centavos = 0  # não descontar de novo
                else:
                    total_reembolsado += refund_centavos
                    pedidos_com_reembolso.append({
                        "merchant": order.get("merchant", {}).get("name", "Desconhecido"),
                        "tipo": refund_info.get("type", "REFUND"),
                        "valor": refund_centavos,
                    })

        elif hc_refund_centavos > 0:
            # Só historyChanges, sem order.refund.
            total_reembolsado += hc_refund_centavos
            pedidos_com_reembolso.append({
                "merchant": order.get("merchant", {}).get("name", "Desconhecido"),
                "tipo": "ITEM_REMOVED (itens removidos)",
                "valor": hc_refund_centavos,
            })

        net_payment = payment_total - refund_centavos
        total_gasto += net_payment

        if net_payment > valor_pedido_mais_caro:
            valor_pedido_mais_caro = net_payment
            pedido_mais_caro = order

        created_at = order.get("createdAt", "")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                gastos_por_mes[dt.strftime("%Y-%m")] += net_payment
                gastos_por_ano[dt.strftime("%Y")] += net_payment
            except ValueError:
                pass

        bag = order.get("bag", {})
        delivery_fee = bag.get("deliveryFee", {}).get("valueWithDiscount", 0)
        total_delivery_fee += delivery_fee

        total_original = bag.get("total", {}).get("value", 0)
        total_com_desconto = bag.get("total", {}).get("valueWithDiscount", 0)
        desconto = total_original - total_com_desconto
        if desconto > 0:
            total_descontos += desconto

        for fee in order.get("fees", []):
            total_service_fee += fee.get("amount", {}).get("value", 0)

        merchant = order.get("merchant", {})
        merchant_name = merchant.get("name", "Desconhecido")
        merchant_type = merchant.get("type", "UNKNOWN")
        gastos_por_merchant[merchant_name]["total"] += net_payment
        gastos_por_merchant[merchant_name]["count"] += 1
        gastos_por_tipo_merchant[merchant_type] += net_payment

        methods = order.get("payments", {}).get("methods", [])
        methods_sum = sum(m.get("amount", {}).get("value", 0) for m in methods)
        scale = (net_payment / methods_sum) if methods_sum > 0 else 0
        for method in methods:
            method_desc = method.get("method", {}).get("description", "Outro")
            brand_desc = method.get("brand", {}).get("description", "")
            key = method_desc + (f" ({brand_desc})" if brand_desc and brand_desc != method_desc else "")
            gastos_por_metodo[key] += round(method.get("amount", {}).get("value", 0) * scale)

        driver = (order.get("delivery", {}) or {}).get("driver") or {}
        driver_id = driver.get("id")
        if driver_id:
            entry = entregas_por_entregador[driver_id]
            entry["nome"] = driver.get("name", "Entregador desconhecido")
            entry["count"] += 1

        pedidos_detalhados.append({
            "id": order.get("orderNumber", ""),
            "merchant": merchant_name,
            "type": merchant_type,
            "date": created_at,
            "value": net_payment,
            "refund": refund_centavos,
        })

    print()
    print("=" * 60)
    print("RELATÓRIO DE GASTOS NO IFOOD")
    print("=" * 60)

    print(f"\nRESUMO GERAL")
    print(f"Total de pedidos retornados pela API: {len(orders)}")
    print(f"Pedidos considerados no relatório (CONCLUDED): {pedidos_considerados}")
    for status, count in sorted(status_count.items()):
        marca = "" if status == "CONCLUDED" else "  (excluído do relatório)"
        print(f"{status}: {count}{marca}")

    print(f"\nTOTAL GASTO (líquido, já descontando reembolsos): {format_brl(total_gasto)}")
    print(f"Total cobrado (bruto): {format_brl(total_bruto)}")
    print(f"Total reembolsado: {format_brl(total_reembolsado)}")
    print(f"Taxas de entrega: {format_brl(total_delivery_fee)}")
    print(f"Taxas de serviço: {format_brl(total_service_fee)}")
    print(f"Descontos ganhos: {format_brl(total_descontos)}")
    if pedidos_considerados > 0:
        print(f"Média por pedido: {format_brl(total_gasto // pedidos_considerados)}")

    if pedido_mais_caro is not None:
        print(f"\nPEDIDO MAIS CARO DO HISTÓRICO")
        imprimir_detalhes_pedido(pedido_mais_caro, valor_pedido_mais_caro)

    if pedidos_com_reembolso:
        print(f"\nPEDIDOS COM REEMBOLSO ({len(pedidos_com_reembolso)})")
        for p in pedidos_com_reembolso:
            print(f"{p['merchant']}: -{format_brl(p['valor'])} ({p['tipo']})")

    if gastos_por_tipo_merchant:
        print(f"\nGASTOS POR CATEGORIA")
        type_labels = {
            "RESTAURANT": "Restaurantes",
            "PHARMACY": "Farmácias",
            "BEVERAGE": "Bebidas",
            "GROCERY": "Mercado",
            "PET": "Pet",
        }
        for tipo, valor in sorted(gastos_por_tipo_merchant.items(), key=lambda x: x[1], reverse=True):
            label = type_labels.get(tipo, tipo)
            print(f"{label}: {format_brl(valor)}")

    if gastos_por_ano:
        print(f"\nGASTOS POR ANO")
        for ano in sorted(gastos_por_ano.keys()):
            print(f"{ano}: {format_brl(gastos_por_ano[ano])}")

    if gastos_por_mes:
        print(f"\nGASTOS POR MÊS")
        for mes in sorted(gastos_por_mes.keys()):
            print(f"{mes}: {format_brl(gastos_por_mes[mes])}")

    if gastos_por_merchant:
        print(f"\nTOP ESTABELECIMENTOS")
        sorted_merchants = sorted(gastos_por_merchant.items(), key=lambda x: x[1]["total"], reverse=True)
        for i, (name, data) in enumerate(sorted_merchants[:15], 1):
            pedidos_txt = f"{data['count']} pedido{'s' if data['count'] > 1 else ''}"
            print(f"{i:2d}. {name}")
            print(f"{format_brl(data['total'])} ({pedidos_txt})")

    # Só entra no ranking quem entregou 2 ou mais vezes
    ranking_entregadores = sorted(
        (d for d in entregas_por_entregador.values() if d["count"] >= 2),
        key=lambda x: x["count"],
        reverse=True,
    )
    if ranking_entregadores:
        print(f"\nTOP ENTREGADORES")
        for i, data in enumerate(ranking_entregadores[:15], 1):
            entregas_txt = f"{data['count']} entrega{'s' if data['count'] != 1 else ''}"
            print(f"{i:2d}. {data['nome']} — {entregas_txt}")

    if gastos_por_metodo:
        print(f"\nPOR MÉTODO DE PAGAMENTO")
        for metodo, valor in sorted(gastos_por_metodo.items(), key=lambda x: x[1], reverse=True):
            print(f"{metodo}: {format_brl(valor)}")

    print("\n" + "=" * 60)

    output_file = "ifood_pedidos.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    print(f"\nDados salvos em: {os.path.abspath(output_file)}")

    dashboard_ctx = {
        "total_gasto": total_gasto,
        "total_bruto": total_bruto,
        "total_reembolsado": total_reembolsado,
        "total_delivery_fee": total_delivery_fee,
        "total_service_fee": total_service_fee,
        "total_descontos": total_descontos,
        "pedidos_considerados": pedidos_considerados,
        "status_count": dict(status_count),
        "gastos_por_mes": dict(sorted(gastos_por_mes.items())),
        "gastos_por_merchant": dict(gastos_por_merchant),
        "gastos_por_tipo_merchant": dict(gastos_por_tipo_merchant),
        "gastos_por_metodo": dict(gastos_por_metodo),
        "pedidos_com_reembolso": pedidos_com_reembolso,
        "pedidos_detalhados": sorted(pedidos_detalhados, key=lambda p: p["date"], reverse=True),
        "pedido_mais_caro": montar_dados_pedido_mais_caro(pedido_mais_caro, valor_pedido_mais_caro),
        "top_entregadores": sorted(
            [
                {"nome": d["nome"], "count": d["count"]}
                for d in entregas_por_entregador.values()
                if d["count"] >= 2
            ],
            key=lambda x: x["count"],
            reverse=True,
        )[:15],
    }
    dashboard_file = "ifood_dashboard.html"
    with open(dashboard_file, "w", encoding="utf-8") as f:
        f.write(generate_dashboard_html(dashboard_ctx))
    print(f"Dashboard gerado em: {os.path.abspath(dashboard_file)}")
    print(f"Abra esse arquivo no navegador para visualizar.")


async def main():
    print("=" * 60)
    print("CALCULADORA DE GASTOS NO IFOOD (Automático)")
    print("=" * 60)
    print()
    print("O Chrome vai abrir automaticamente.")
    print("Se você não estiver logado, faça login no navegador.")
    print("O script captura tudo sozinho.")
    print()

    bearer_token, cf_clearance, captured_headers = await capture_tokens()

    if not bearer_token:
        print("\nFalha ao capturar credenciais. Abortando.")
        return

    print(f"\nCredenciais capturadas com sucesso!")
    print(f"Bearer: {bearer_token[:50]}...")
    if cf_clearance:
        print(f"cf_clearance: {cf_clearance[:30]}...")

    print(f"\nBuscando todos os pedidos...\n")
    orders = fetch_all_orders(bearer_token, cf_clearance, captured_headers)

    if orders:
        print(f"\n{len(orders)} pedidos encontrados!")
        analyze_orders(orders)
    else:
        print("\nNenhum pedido retornado.")


if __name__ == "__main__":
    uc.loop().run_until_complete(main())
