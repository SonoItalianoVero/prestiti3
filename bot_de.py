# -*- coding: utf-8 -*-
"""
HigobiGMBH – Internal Telegram Bot (DE/AT)
Операторский бот (RU интерфейс) -> PDF (DE).
Зависимости: python-telegram-bot==20.7, reportlab
Ассеты (имена точные):
  ./assets/HIGOBI_LOGO.PNG
  ./assets/santander1.png
  ./assets/santander2.png
  ./assets/wagnersign.png
  ./assets/duraksign.png
Шрифты (опционально):
  ./fonts/PTMono-Regular.ttf
  ./fonts/PTMono-Bold.ttf
"""

from __future__ import annotations
from reportlab.graphics.shapes import Drawing, Rect, Circle
from PIL import Image as PILImage
from reportlab.graphics import renderPDF
import os, re, logging, io
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

# ---- logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("higobi-de")

# ---- reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Image, KeepTogether
)
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------- TIME ----------
TZ_DE = ZoneInfo("Europe/Berlin")
def now_de_date() -> str:
    return datetime.now(TZ_DE).strftime("%d.%m.%Y")

# ---------- FONTS ----------
try:
    pdfmetrics.registerFont(TTFont("PTMono", "fonts/PTMono-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("PTMono-Bold", "fonts/PTMono-Bold.ttf"))
    F_MONO = "PTMono"; F_MONO_B = "PTMono-Bold"
except Exception:
    F_MONO = "Courier"; F_MONO_B = "Courier-Bold"

# ---------- COMPANY / CONSTANTS ----------
COMPANY = {
    "brand": "HIGOBI",
    "legal": "HIGOBI Immobilien GMBH",
    "addr":  "Johann-Georg-Schlosser-Straße 11, 76149 Karlsruhe, Deutschland",
    "reg":   "Handelsregister: HRB 755353; Stammkapital: 25.002,00 EUR",
    "rep":   "",  # при необходимости подставьте Geschäftsführer
    "contact": "Telegram @higobi_de_at_bot",
    "email": "higobikontakt@inbox.eu",
    "web": "higobi-gmbh.de",
    "business_scope": (
        "Die Verwaltung von Grundbesitz aller Art einschließlich der Tätigkeit als Verwalter nach § 26a WEG "
        "sowie die Mietverwaltung, die Erstellung von Betriebskostenabrechnungen, der Kauf, Verkauf, die Vermietung, "
        "Entwicklung, Beratung und Projektierung von Immobilien und Grundstücken aller Art (Makler und "
        "Darlehensvermittler i.S. des § 34c Abs. 1 Satz 1 Nr. 1 und 2 GewO), die Immobiliardarlehensvermittlung "
        "i.S. des § 34i GewO, die Erstellung von Immobiliengutachten, die Entrümpelung, die Tatortreinigung."
    ),
}

SEPA = {"ci": "DE98ZZZ00123950001", "prenotice_days": 7}

# ---------- BANK PROFILES ----------
BANKS = {
    "DE": {
        "name": "Santander Consumer Bank AG",
        "addr": "Budapester Str. 37, 10787 Berlin",
    },
    "AT": {
        "name": "Santander Consumer Bank GmbH",
        "addr": "Wagramer Straße 19, 1220 Wien",
    },
}
def get_bank_profile(cc: str) -> dict:
    return BANKS.get(cc.upper(), BANKS["DE"])

def asset_path(*candidates: str) -> str:
    """Вернёт первый существующий файл из candidates в ./assets/. Игнор регистра/вариантов."""
    for name in candidates:
        p = os.path.join("assets", name)
        if os.path.exists(p):
            return p
    # если ничего не нашли — вернём первый путь (img_box сам залогирует предупреждение)
    return os.path.join("assets", candidates[0])


# ---------- ASSETS ----------
ASSETS = {
    "logo_cred":     asset_path("HIGOBI_LOGO.PNG", "higobi_logo.png", "higobi_logo.PNG", "HIGOBI_logo.png"),
    "logo_partner1": asset_path("santander1.png", "SANTANDER1.PNG"),
    "logo_partner2": asset_path("santander2.png", "SANTANDER2.PNG"),
    "sign_bank":     asset_path("wagnersign.png", "wagnersign.PNG"),
    "sign_c2g":      asset_path("duraksign.png", "duraksign.PNG"),
    "exclam":        asset_path("exclam.png", "exclam.PNG"),
}

# ---------- UI ----------
BTN_CONTRACT = "Сделать контракт"
BTN_SEPA     = "Создать мандат SDD"
BTN_AML      = "Письмо АМЛ/комплаенс"
BTN_CARD     = "Выдача на карту"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_CONTRACT), KeyboardButton(BTN_SEPA)],
        [KeyboardButton(BTN_AML), KeyboardButton(BTN_CARD)],
    ],
    resize_keyboard=True,
)

# ---------- HELPERS ----------
def fmt_eur(v: float | Decimal) -> str:
    if isinstance(v, Decimal): v = float(v)
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"

def parse_num(txt: str) -> float:
    t = txt.strip().replace(" ", "").replace(".", "").replace(",", ".")
    return float(t)

def monthly_payment(principal: float, tan_percent: float, months: int) -> float:
    if months <= 0: return 0.0
    r = (tan_percent / 100.0) / 12.0
    if r == 0: return principal / months
    return principal * (r / (1 - (1 + r) ** (-months)))

def img_box(path: str, max_h: float) -> Image | None:
    if not os.path.exists(path):
        log.warning("IMAGE NOT FOUND: %s", os.path.abspath(path)); return None
    try:
        ir = ImageReader(path); iw, ih = ir.getSize()
        scale = max_h / float(ih)
        return Image(path, width=iw * scale, height=ih * scale)
    except Exception as e:
        log.error("IMAGE LOAD ERROR %s: %s", path, e); return None

def logo_flatten_trim(path: str, max_h: float) -> Image | None:
    if not os.path.exists(path):
        log.warning("IMAGE NOT FOUND: %s", path); return None
    try:
        im = PILImage.open(path).convert("RGBA")
        alpha = im.split()[-1]
        bbox = alpha.getbbox()
        if bbox:
            im = im.crop(bbox)
            alpha = im.split()[-1]
        bg = PILImage.new("RGB", im.size, "#FFFFFF")
        bg.paste(im, mask=alpha)

        bio = io.BytesIO()
        bg.save(bio, format="PNG", optimize=True)
        bio.seek(0)

        ir = ImageReader(bio)
        iw, ih = ir.getSize()
        scale = max_h / float(ih)
        return Image(bio, width=iw * scale, height=ih * scale)
    except Exception as e:
        log.error("LOGO CLEAN ERROR %s: %s", path, e)
        return None

def exclam_flowable(h_px: float = 28) -> renderPDF.GraphicsFlowable:
    h = float(h_px); w = h * 0.42
    d = Drawing(w, h)
    bar_w = w * 0.36; bar_h = h * 0.68; bar_x = (w - bar_w) / 2.0; bar_y = h * 0.20
    d.add(Rect(bar_x, bar_y, bar_w, bar_h, rx=bar_w * 0.25, ry=bar_w * 0.25,
               fillColor=colors.HexColor("#D73737"), strokeWidth=0))
    r = w * 0.18
    d.add(Circle(w / 2.0, h * 0.10, r, fillColor=colors.HexColor("#D73737"), strokeWidth=0))
    return renderPDF.GraphicsFlowable(d)

def draw_border_and_pagenum(canv, doc):
    w, h = A4
    canv.saveState()
    m = 10 * mm; inner = 6
    canv.setStrokeColor(colors.HexColor("#0E2A47")); canv.setLineWidth(2)
    canv.rect(m, m, w - 2*m, h - 2*m, stroke=1, fill=0)
    canv.rect(m+inner, m+inner, w - 2*(m+inner), h - 2*(m+inner), stroke=1, fill=0)
    canv.setFont(F_MONO, 9); canv.setFillColor(colors.black)
    canv.drawCentredString(w/2.0, 5*mm, str(canv.getPageNumber()))
    canv.restoreState()

# ---------- STATES ----------
ASK_COUNTRY = 10
ASK_CLIENT, ASK_AMOUNT, ASK_TAN, ASK_EFF, ASK_TERM = range(20, 25)
(SDD_NAME, SDD_ADDR, SDD_CITY, SDD_COUNTRY, SDD_ID, SDD_IBAN, SDD_BIC) = range(100, 107)
(AML_NAME, AML_ID, AML_IBAN) = range(200, 203)
(CARD_NAME, CARD_ADDR) = range(300, 302)

# ---------- CONTRACT PDF ----------
def build_contract_pdf(values: dict) -> bytes:
    client = (values.get("client", "") or "").strip()
    amount = float(values.get("amount", 0) or 0)
    tan    = float(values.get("tan", 0) or 0)
    eff    = float(values.get("eff", 0) or 0)
    term   = int(values.get("term", 0) or 0)

    bank_name = values.get("bank_name") or "Santander Consumer Bank"
    bank_addr = values.get("bank_addr") or ""

    rate = monthly_payment(amount, tan, term)
    interest = max(rate * term - amount, 0)
    total = amount + interest

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1Mono",  fontName=F_MONO_B, fontSize=15.6, leading=17.6, spaceAfter=4))
    styles.add(ParagraphStyle(name="H2Mono",  fontName=F_MONO_B, fontSize=12.6, leading=14.6, spaceBefore=6, spaceAfter=3))
    styles.add(ParagraphStyle(name="Mono",    fontName=F_MONO,   fontSize=10.4, leading=12.2))
    styles.add(ParagraphStyle(name="MonoSm",  fontName=F_MONO,   fontSize=9.8,  leading=11.4))
    styles.add(ParagraphStyle(name="MonoXs",  fontName=F_MONO,   fontSize=9.0,  leading=10.4))
    styles.add(ParagraphStyle(name="RightXs", fontName=F_MONO,   fontSize=9.0,  leading=10.4, alignment=2))
    styles.add(ParagraphStyle(name="SigHead", fontName=F_MONO,   fontSize=11.2, leading=13.0, alignment=1))

    story = []

    # --- Шапка с логотипами
    row_cells = [
        img_box(ASSETS["logo_partner1"], 24*mm) or Spacer(1, 24*mm),
        logo_flatten_trim(ASSETS["logo_partner2"], 24*mm),
        img_box(ASSETS["logo_cred"],    24*mm) or Spacer(1, 24*mm),
    ]
    logos_tbl = Table([row_cells], colWidths=[doc.width*0.55, doc.width*0.22, doc.width*0.23])
    logos_tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(0,0),"LEFT"),
        ("ALIGN",(1,0),(2,0),"RIGHT"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story += [logos_tbl, Spacer(1, 4)]

    # --- Титул
    story.append(Paragraph(f"{bank_name} – Vorabinformation / Vorvertrag #2690497", styles["H1Mono"]))
    story.append(Paragraph(f"Vermittlung: {COMPANY['legal']}, {COMPANY['addr']}", styles["MonoSm"]))

    reg_parts = [COMPANY["reg"]]
    if COMPANY.get("rep"):
        reg_parts.append(COMPANY["rep"])
    story.append(Paragraph(" – ".join(reg_parts), styles["MonoSm"]))

    contact_line = f"Kontakt: {COMPANY['contact']} | E-Mail: {COMPANY['email']} | Web: {COMPANY['web']}"
    story.append(Paragraph(contact_line, styles["MonoSm"]))

    # НОВОЕ: имя клиента на титуле
    if client:
        story.append(Paragraph(f"Kunde: <b>{client}</b>", styles["MonoSm"]))

    story.append(Paragraph(f"Erstellt: {now_de_date()}", styles["RightXs"]))
    story.append(Spacer(1, 2))

    # --- Статус-бокс
    status_tbl = Table([
        [Paragraph("<b>Status der Anfrage:</b>", styles["Mono"]),
         Paragraph("<b>BESTÄTIGT</b> (Bankbestätigung liegt vor)", styles["Mono"])],
        [Paragraph("<b>Dokument-Typ:</b>", styles["Mono"]),
         Paragraph("<b>Bestätigter Vertrag</b>", styles["Mono"])],
        [Paragraph("<b>Noch ausstehend:</b>", styles["Mono"]),
         Paragraph("Unterzeichnung dieses Dokuments, Zahlung der Vermittlungsvergütung, Versand des Tilgungsplans", styles["Mono"])],
        [Paragraph("<b>Auszahlung:</b>", styles["Mono"]),
         Paragraph("nur nach Unterzeichnung des Vertrags und nach Zahlung der Vermittlungsvergütung (170 €).", styles["Mono"])],
    ], colWidths=[43*mm, doc.width-43*mm])
    status_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.9,colors.HexColor("#96A6C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EEF3FF")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [KeepTogether(status_tbl), Spacer(1, 4)]

    # --- Параметры
    params = [
        ["Parameter", "Details"],
        ["Nettodarlehensbetrag", fmt_eur(amount)],
        ["Sollzinssatz (p.a.)",  f"{tan:.2f} %"],
        ["Effektiver Jahreszins (p.a.)", f"{eff:.2f} %"],
        ["Laufzeit",             f"{term} Monate (max. 84)"],
        ["Monatsrate*",          fmt_eur(rate)],
        ["Bearbeitungsgebühr",   "0 €"],
        ["Einzugskosten",        "0 €"],
        ["Verwaltungskosten",    "0 €"],
        ["Versicherungsprämie (falls angefordert)", "280 €"],
        ["Auszahlung",           "30–60 Min nach Unterzeichnung und nach Zahlung der Vermittlungsvergütung (170 €)"],
    ]
    table_rows = []
    for i, (k, v) in enumerate(params):
        if i == 0:
            table_rows.append([Paragraph(f"<b>{k}</b>", styles["Mono"]), Paragraph(f"<b>{v}</b>", styles["Mono"])])
        else:
            table_rows.append([Paragraph(k, styles["Mono"]), Paragraph(str(v), styles["Mono"])])
    tbl = Table(table_rows, colWidths=[75*mm, doc.width-75*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#ececec")),
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("LEFTPADDING",(0,0),(-1,-1),5), ("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),2.0), ("BOTTOMPADDING",(0,0),(-1,-1),2.0),
    ]))
    story += [KeepTogether(tbl), Spacer(1, 2)]
    story.append(Paragraph("*Monatsrate berechnet zum Datum dieses Angebots.", styles["MonoXs"]))
    story.append(Spacer(1, 4))

    # --- Vorteile
    story.append(Paragraph("Vorteile", styles["H2Mono"]))
    for it in [
        "• Möglichkeit, bis zu 3 Raten auszusetzen.",
        "• Vorzeitige Rückzahlung ohne Strafgebühren.",
        "• Zinsreduktion –0,10 %-Pkt. alle 12 pünktlichen Monate (bis mind. 5,95 %).",
        "• Ratenpause bei Arbeitsverlust (vorbehaltlich Bankzustimmung).",
    ]:
        story.append(Paragraph(it, styles["MonoSm"]))

    # --- Sanktionen
    story.append(Paragraph("Sanktionen und Verzugszinsen", styles["H2Mono"]))
    for it in [
        "• Verzug >5 Tage: Sollzins + 2 %-Pkt.",
        "• Mahnung: 10 € postalisch / 5 € digital.",
        "• 2 nicht bezahlte Raten: Vertragsauflösung, Inkasso.",
        "• Vertragsstrafe nur bei Verletzung vertraglicher Pflichten.",
    ]:
        story.append(Paragraph(it, styles["MonoSm"]))

    # ======= PAGE 2 =======
    story.append(PageBreak())

    # --- СТРАНИЦА 2: Kommunikation und Service HIGOBI Immobilien GMBH
    story.append(Paragraph("Kommunikation und Service HIGOBI Immobilien GMBH", styles["H2Mono"]))
    bullets = [
        "• Sämtliche Kommunikation zwischen Bank und Kunden erfolgt ausschließlich über HIGOBI Immobilien GMBH.",
        "• Vertrag und Anlagen werden als PDF via Telegram übermittelt.",
        "• Vermittlungsvergütung HIGOBI Immobilien GMBH: fixe Servicepauschale 170 € (kein Bankentgelt).",
        "• Auszahlung der Kreditmittel erfolgt streng erst nach Unterzeichnung des Vertrags und nach Zahlung der Vermittlungsvergütung (170 €).",
        "• Zahlungskoordinaten werden dem Kunden individuell durch den zuständigen HIGOBI-Manager mitgeteilt (keine Vorauszahlungen an Dritte).",
    ]
    for b in bullets:
        story.append(Paragraph(b, styles["MonoSm"]))

    story.append(Spacer(1, 6))

    # --- FAQ
    faq = ('Häufige Frage: „Vorabgenehmigung = endgültige Genehmigung?“ '
           'Antwort: Die Kreditvergabe ist bestätigt; dieses Dokument enthält die bestätigten Vertragsdaten.')
    faq_box = Table([[Paragraph(faq, styles["MonoSm"])]], colWidths=[doc.width])
    faq_box.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.9,colors.HexColor("#96A6C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EEF3FF")),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [faq_box, Spacer(1, 6)]

    # --- Экономический обзор
    riepilogo = Table([
        [Paragraph("Nettodarlehen", styles["Mono"]), Paragraph(fmt_eur(amount), styles["Mono"])],
        [Paragraph("Geschätzte Zinsen (Laufzeit)", styles["Mono"]), Paragraph(fmt_eur(interest), styles["Mono"])],
        [Paragraph("Einmalige Kosten", styles["Mono"]), Paragraph("0 €", styles["Mono"])],
        [Paragraph("Einzugskosten", styles["Mono"]), Paragraph("0 €", styles["Mono"])],
        [Paragraph("Gesamtschuld (Schätzung)", styles["Mono"]), Paragraph(fmt_eur(total), styles["Mono"])],
        [Paragraph("Laufzeit", styles["Mono"]), Paragraph(f"{term} Monate", styles["Mono"])],
    ], colWidths=[75*mm, doc.width-75*mm])
    riepilogo.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("BACKGROUND",(0,0),(-1,-1),colors.whitesmoke),
        ("LEFTPADDING",(0,0),(-1,-1),5), ("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),2.0), ("BOTTOMPADDING",(0,0),(-1,-1),2.0),
    ]))
    story += [KeepTogether(riepilogo), Spacer(1, 6)]

    # --- Подписи
    story.append(Paragraph("Unterschriften", styles["H2Mono"]))
    head_l = Paragraph("Unterschrift Kunde", styles["SigHead"])
    head_c = Paragraph("Unterschrift Vertreter<br/>Bank", styles["SigHead"])
    head_r = Paragraph("Unterschrift Vertreter<br/>HIGOBI Immobilien GMBH", styles["SigHead"])
    sig_bank = img_box(ASSETS["sign_bank"], 26*mm)
    sig_c2g  = img_box(ASSETS["sign_c2g"],  26*mm)
    SIG_ROW_H = 30*mm
    sig_tbl = Table(
        [
            [head_l, head_c, head_r],
            ["", sig_bank or Spacer(1, SIG_ROW_H), sig_c2g or Spacer(1, SIG_ROW_H)],
            ["", "", ""],
        ],
        colWidths=[doc.width/3.0, doc.width/3.0, doc.width/3.0],
        rowHeights=[12*mm, SIG_ROW_H, 8*mm],
        hAlign="CENTER",
    )
    sig_tbl.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,-1),F_MONO),
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("VALIGN",(0,1),(-1,1),"BOTTOM"),
        ("BOTTOMPADDING",(0,1),(-1,1),-6),
        ("LINEBELOW",(0,2),(0,2),1.1,colors.black),
        ("LINEBELOW",(1,2),(1,2),1.1,colors.black),
        ("LINEBELOW",(2,2),(2,2),1.1,colors.black),
    ]))
    story.append(sig_tbl)

    doc.build(story, onFirstPage=draw_border_and_pagenum, onLaterPages=draw_border_and_pagenum)
    buf.seek(0)
    return buf.read()

# ---------- SEPA PDF ----------
class Typesetter:
    def __init__(self, canv, left=18*mm, top=None, line_h=14.2):
        self.c = canv
        self.left = left
        self.x = left
        self.y = top if top is not None else A4[1] - 18*mm
        self.line_h = line_h
        self.font_r = F_MONO
        self.font_b = F_MONO_B
        self.size = 11
    def _w(self, s, bold=False, size=None):
        size = size or self.size
        return pdfmetrics.stringWidth(s, self.font_b if bold else self.font_r, size)
    def nl(self, n=1):
        self.x = self.left; self.y -= self.line_h * n
    def seg(self, t, bold=False, size=None):
        size = size or self.size
        self.c.setFont(self.font_b if bold else self.font_r, size)
        self.c.drawString(self.x, self.y, t)
        self.x += self._w(t, bold, size)
    def line(self, t="", bold=False, size=None):
        self.seg(t, bold, size); self.nl()
    def para(self, text, bold=False, size=None, indent=0, max_w=None):
        size = size or self.size
        max_w = max_w or (A4[0] - self.left*2)
        words = text.split()
        line = ""; first = True
        while words:
            w = words[0]; trial = (line + " " + w).strip()
            if self._w(trial, bold, size) <= max_w - (indent if first else 0):
                line = trial; words.pop(0)
            else:
                self.c.setFont(self.font_b if bold else self.font_r, size)
                x0 = self.left + (indent if first else 0)
                self.c.drawString(x0, self.y, line)
                self.y -= self.line_h; first = False; line = ""
        if line:
            self.c.setFont(self.font_b if bold else self.font_r, size)
            x0 = self.left + (indent if first else 0)
            self.c.drawString(x0, self.y, line)
            self.y -= self.line_h
    def kv(self, label, value, size=None, max_w=None):
        size = size or self.size
        max_w = max_w or (A4[0] - self.left*2)
        label_txt = f"{label}: "; lw = self._w(label_txt, True, size)
        self.c.setFont(self.font_b, size); self.c.drawString(self.left, self.y, label_txt)
        rem_w = max_w - lw; old_left = self.left; self.left += lw
        self.para(value, bold=False, size=size, indent=0, max_w=rem_w)
        self.left = old_left

def sepa_build_pdf(values: dict) -> bytes:
    """SEPA SDD Mandate — без логотипов, с авто-переносом и адресом банка."""
    name = (values.get("name","") or "").strip() or "______________________________"
    addr = (values.get("addr","") or "").strip() or "_______________________________________________________"
    capcity = (values.get("capcity","") or "").strip() or "__________________________________________"
    country = (values.get("country","") or "").strip() or "____________________"
    idnum = (values.get("idnum","") or "").strip() or "________________"
    iban = ((values.get("iban","") or "").replace(" ", "")) or "__________________________________"
    bic  = (values.get("bic","") or "").strip() or "___________"

    date_de = now_de_date()
    umr = f"HIGOBI-{datetime.now().year}-2690497"

    bank_name = values.get("bank_name") or "Santander Consumer Bank"
    bank_addr = values.get("bank_addr") or ""

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    ts = Typesetter(c, left=18*mm, top=A4[1]-22*mm, line_h=14.2)
    ts.size = 11

    # Заголовок
    ts.line("SEPA-Lastschriftmandat (SDD)", bold=True)
    ts.seg("Schema: ", True); ts.seg("Y CORE   X B2B   ")
    ts.seg("Zahlungsart: ", True); ts.line("Y Wiederkehrend   X Einmalig")

    # CI / UMR
    ts.kv("Gläubiger-Identifikationsnummer (CI)", SEPA["ci"])
    ts.kv("Mandatsreferenz (UMR)", umr)
    ts.nl()

    # Zahlerdaten
    ts.line("Zahlerdaten (Kontoinhaber)", bold=True)
    ts.kv("Name/Firma", name)
    ts.kv("Anschrift", addr)
    ts.kv("PLZ / Ort / Bundesland", capcity)
    ts.kv("Land", country + "    Ausweis-/Steuer-Nr.: " + idnum)
    ts.kv("IBAN (ohne Leerzeichen)", iban)
    ts.kv("BIC", bic)
    ts.nl()

    # Ermächtigung
    ts.line("Ermächtigung", bold=True)
    ts.para(
        "Mit meiner Unterschrift ermächtige ich (A) "
        f"{bank_name}, an meine Bank Lastschriftaufträge zu senden und (B) "
        "meine Bank, mein Konto gemäß den Anweisungen des Kreditgebers zu belasten.",
    )
    ts.para(
        "Für das Schema CORE habe ich das Recht, bei meiner Bank die Erstattung "
        "innerhalb von 8 Wochen ab Belastungsdatum zu verlangen.",
    )
    ts.kv("Pre-Notification", f"{SEPA['prenotice_days']} Tage vor Fälligkeit")
    ts.kv("Datum", date_de)
    ts.para("Unterschrift des Zahlers: nicht erforderlich; Dokumente werden durch den Intermediär vorbereitet.")
    ts.nl()

    # Daten des Gläubigers
    ts.line("Daten des Gläubigers", bold=True)
    ts.kv("Bezeichnung", bank_name)
    ts.kv("Adresse", bank_addr)
    ts.kv("SEPA CI", SEPA["ci"])
    ts.nl()

    # Intermediär
    ts.line("Beauftragter für die Sammlung des Mandats (Intermediär)", bold=True)
    ts.kv("Name", COMPANY["legal"])
    ts.kv("Adresse", COMPANY["addr"])
    ts.kv("Kontakt", f"{COMPANY['contact']} | E-Mail: {COMPANY['email']} | Web: {COMPANY['web']}")
    ts.nl()

    # Optionale Klauseln
    ts.line("Optionale Klauseln", bold=True)
    ts.para("[Y] Ich erlaube die elektronische Aufbewahrung dieses Mandats.")
    ts.para("[Y] Bei Änderung der IBAN oder Daten verpflichte ich mich, dies schriftlich mitzuteilen.")
    ts.para("[Y] Widerruf: Das Mandat kann durch Mitteilung an den Kreditgeber und meine Bank widerrufen werden; "
            "der Widerruf hat Wirkung auf zukünftige Abbuchungen.")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()

# ---------- AML LETTER ----------
def aml_build_pdf(values: dict) -> bytes:
    """
    Zahlungsaufforderung (vom Kreditgeber).
    Стр.1: до п.3 (с предупреждающей плашкой вверху). Стр.2: с п.4 и до конца.
    (Без реквизитов — их предоставляет менеджер HIGOBI.)
    """
    name = (values.get("aml_name","") or "").strip() or "[_____________________________]"
    idn  = (values.get("aml_id","") or "").strip() or "[________________]"
    iban = ((values.get("aml_iban","") or "").replace(" ","")) or "[_____________________________]"
    date_de = now_de_date()

    VORGANG_NR = "2690497"
    PAY_DEADLINE   = 7
    PAY_AMOUNT     = Decimal("280.00")

    bank_name = values.get("bank_name") or "Santander Consumer Bank"
    bank_addr = values.get("bank_addr") or ""
    BANK_DEPT  = "Abteilung Sicherheit & Antibetrug"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=14*mm, bottomMargin=14*mm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H",      fontName=F_MONO_B, fontSize=13.4, leading=15.2, spaceAfter=4))
    styles.add(ParagraphStyle(name="Hsub",   fontName=F_MONO,   fontSize=10.2, leading=12.0, textColor=colors.HexColor("#334")))
    styles.add(ParagraphStyle(name="H2",     fontName=F_MONO_B, fontSize=12.2, leading=14.0, spaceBefore=5, spaceAfter=3))
    styles.add(ParagraphStyle(name="Mono",   fontName=F_MONO,   fontSize=10.6, leading=12.6))
    styles.add(ParagraphStyle(name="MonoSm", fontName=F_MONO,   fontSize=10.0, leading=11.8))
    styles.add(ParagraphStyle(name="Key",    fontName=F_MONO_B, fontSize=10.6, leading=12.6))
    styles.add(ParagraphStyle(name="Box",    fontName=F_MONO,   fontSize=10.2, leading=12.0))

    # ---------- страница 1 ----------
    page1 = []

    # Лого Santander по центру
    logo = img_box(ASSETS["logo_partner1"], 26*mm)
    if logo:
        logo.hAlign = "CENTER"
        page1 += [logo, Spacer(1, 6)]

    # Шапка письма
    page1.append(Paragraph(f"{bank_name} – Zahlungsaufforderung", styles["H"]))
    page1.append(Paragraph(BANK_DEPT, styles["Hsub"]))
    page1.append(Paragraph(f"Vorgang-Nr.: {VORGANG_NR}", styles["MonoSm"]))
    page1.append(Paragraph(f"Datum: {date_de}", styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    # --- ПРЕАМБУЛА ---
    warn_icon_l = exclam_flowable(10 * mm)
    warn_icon_r = exclam_flowable(10 * mm)
    preamble_text = (
        "Nach einer erneuten internen Prüfung (deren Verfahren und Methodik nicht offengelegt werden) "
        "wurde Ihr Profil vom Kreditgeber einer erhöhten Wahrscheinlichkeit von Zahlungsverzug bzw. "
        "-ausfall zugeordnet. Zur Risikosteuerung und zur Fortführung des Auszahlungsprozesses ist eine "
        f"<b>Garantiezahlung/Versicherungsprämie in Höhe von {fmt_eur(PAY_AMOUNT)}</b> erforderlich, zahlbar "
        f"<b>innerhalb von {PAY_DEADLINE} Werktagen</b>."
    )
    pre_tbl = Table(
        [[warn_icon_l or "", Paragraph(preamble_text, styles["MonoSm"]), warn_icon_r or ""]],
        colWidths=[12*mm, doc.width - 24*mm, 12*mm]
    )
    pre_tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(0,0),"CENTER"),
        ("ALIGN",(2,0),(2,0),"CENTER"),
        ("BOX",(0,0),(-1,-1),0.8,colors.HexColor("#E0A800")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#FFF7E6")),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),6),  ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    page1 += [pre_tbl, Spacer(1, 6)]

    # Адресат
    page1.append(Paragraph(f"<b>Adressat (Intermediär):</b> {COMPANY['legal']}", styles["Mono"]))
    page1.append(Paragraph(COMPANY["addr"], styles["MonoSm"]))
    page1.append(Paragraph(f"Kontakt: {COMPANY['contact']} | E-Mail: {COMPANY['email']} | Web: {COMPANY['web']}",
                           styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    # Вступление
    page1.append(Paragraph(
        "Im Anschluss an eine ergänzende interne Prüfung zum oben genannten Vorgang teilen wir Folgendes mit.",
        styles["Mono"]
    ))
    page1.append(Spacer(1, 5))

    # Данные заявителя
    page1.append(Paragraph("Daten des Antragstellers (zur Identifizierung)", styles["H2"]))
    for line in [
        f"• <b>Name und Nachname:</b> {name}",
        f"• <b>ID/Steuer-Nr. (falls vorhanden):</b> {idn}",
        f"• <b>IBAN des Kunden:</b> {iban}",
    ]:
        page1.append(Paragraph(line, styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    # 1) Zahlung angefordert
    page1.append(Paragraph("1) Zahlung angefordert", styles["H2"]))
    for b in [
        "• <b>Typologie:</b> Garantiezahlung / Versicherungsprämie",
        f"• <b>Betrag:</b> {fmt_eur(PAY_AMOUNT)}",
        f"• <b>Frist der Ausführung:</b> innerhalb von {PAY_DEADLINE} Werktagen ab Erhalt dieses Schreibens",
        "• <b>Ausführungsweise:</b> Zahlungskoordinaten werden dem Kunden unmittelbar vom zuständigen "
        "Manager der HIGOBI Immobilien GMBH mitgeteilt (keine Zahlungen an Dritte).",
        "• <b>Zahlungspflichtiger:</b> der Antragsteller (Kunde)",
    ]:
        page1.append(Paragraph(b, styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    # 2) Natur der Anforderung
    page1.append(Paragraph("2) Natur der Anforderung", styles["H2"]))
    page1.append(Paragraph(
        "Diese Anforderung ist verpflichtend, vorgelagert und nicht verhandelbar. "
        "Die betreffende Zahlung ist eine notwendige Voraussetzung für die Fortführung des Auszahlungsprozesses.",
        styles["MonoSm"]
    ))
    page1.append(Spacer(1, 5))

    # 3) Pflichten des Intermediärs
    page1.append(Paragraph("3) Pflichten des Intermediärs", styles["H2"]))
    for b in [
        "• Den Antragsteller über dieses Schreiben informieren und Rückmeldung einholen.",
        "• Zahlungskoordinaten bereitstellen und die Vereinnahmung/Weiterleitung gemäß Bankanweisungen vornehmen.",
        "• Zahlungsnachweis (Auftrags-/Quittungskopie) an die Bank übermitteln und mit Kundendaten "
        "(Name und Nachname ↔ IBAN) abgleichen.",
        "• Kommunikation mit der Bank im Namen und für Rechnung des Kunden führen.",
    ]:
        page1.append(Paragraph(b, styles["MonoSm"]))
    page1.append(Spacer(1, 6))

    # ---------- страница 2 ----------
    page2 = []
    page2.append(Paragraph("4) Folgen bei Nichtzahlung", styles["H2"]))
    page2.append(Paragraph(
        "Bei ausbleibender Zahlung innerhalb der genannten Frist lehnt die Bank die Auszahlung einseitig ab "
        "und schließt den Vorgang, mit Widerruf etwaiger Vorbewertungen/Vorbestätigungen und Aufhebung der "
        "zugehörigen wirtschaftlichen Bedingungen.",
        styles["MonoSm"]
    ))
    page2.append(Spacer(1, 6))

    # Информблок вместо реквизитов
    info = ("Zahlungskoordinaten werden dem Kunden direkt vom zuständigen Manager der "
            "HIGOBI Immobilien GMBH bereitgestellt. Bitte leisten Sie keine Zahlungen an Dritte "
            "oder abweichende Konten.")
    info_box = Table([[Paragraph(info, styles["Box"])]], colWidths=[doc.width])
    info_box.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.8,colors.HexColor("#96A6C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EEF3FF")),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    page2.append(info_box)
    page2.append(Spacer(1, 8))

    page2.append(Paragraph(bank_name, styles["Key"]))
    page2.append(Paragraph(BANK_DEPT, styles["MonoSm"]))
    page2.append(Paragraph(f"Adresse: {bank_addr}", styles["MonoSm"]))

    # ---------- сборка ----------
    story = []
    story.extend(page1)
    story.append(PageBreak())
    story.extend(page2)

    doc.build(story, onFirstPage=draw_border_and_pagenum, onLaterPages=draw_border_and_pagenum)
    buf.seek(0)
    return buf.read()

# ---------- CARD DOC ----------
def card_build_pdf(values: dict) -> bytes:
    """
    Santander – Auszahlung per Karte (DE-адаптация 'Erogazione su Carta').
    • Строго 1 страница
    • Логотип Santander сверху по центру
    • Номер дела и UMR зафиксированы:
        - Vorgang-Nr.: 2690497
        - UMR: HIGOBI-<текущий год>-2690497
    """
    name = (values.get("card_name","") or "").strip() or "______________________________"
    addr = (values.get("card_addr","") or "").strip() or "_______________________________________________________"

    case_num = "2690497"
    umr = f"HIGOBI-{datetime.now().year}-2690497"

    date_de = now_de_date()
    bank_name = values.get("bank_name") or "Santander Consumer Bank"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16*mm, rightMargin=16*mm,
        topMargin=14*mm, bottomMargin=14*mm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1",    fontName=F_MONO_B, fontSize=14.2, leading=16.0, spaceAfter=6, alignment=1))
    styles.add(ParagraphStyle(name="H2",    fontName=F_MONO_B, fontSize=12.2, leading=14.0, spaceBefore=6, spaceAfter=4))
    styles.add(ParagraphStyle(name="Mono",  fontName=F_MONO,   fontSize=10.6, leading=12.6))
    styles.add(ParagraphStyle(name="MonoS", fontName=F_MONO,   fontSize=10.0, leading=11.8))
    styles.add(ParagraphStyle(name="Badge", fontName=F_MONO_B, fontSize=10.2, leading=12.0, textColor=colors.HexColor("#0B5D1E"), alignment=1))

    story = []

    logo = img_box(ASSETS["logo_partner1"], 26*mm)
    if logo:
        logo.hAlign = "CENTER"
        story += [logo, Spacer(1, 4)]

    story.append(Paragraph(f"{bank_name} – Auszahlung per Karte", styles["H1"]))
    meta = Table([
        [Paragraph(f"Datum: {date_de}", styles["MonoS"]), Paragraph(f"Vorgang-Nr.: {case_num}", styles["MonoS"])],
    ], colWidths=[doc.width/2.0, doc.width/2.0])
    meta.setStyle(TableStyle([
        ("ALIGN",(0,0),(0,0),"LEFT"), ("ALIGN",(1,0),(1,0),"RIGHT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [meta]

    badge = Table([[Paragraph("BESTÄTIGT – Operatives Dokument", styles["Badge"])]], colWidths=[doc.width])
    badge.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.9,colors.HexColor("#B9E8C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EFFEFA")),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [badge, Spacer(1, 6)]

    intro = (
        "Um die Verfügbarkeit der Mittel noch heute zu gewährleisten und aufgrund nicht erfolgreicher "
        "automatischer Überweisungsversuche wird die Bank – ausnahmsweise – eine "
        "<b>personalisierte Kreditkarte</b> ausstellen, mit Zustellung <b>bis 24:00</b> an die im SDD-Mandat "
        "angegebene Adresse."
    )
    story.append(Paragraph(intro, styles["Mono"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Identifikationsdaten (auszufüllen)", styles["H2"]))
    story.append(Paragraph(f"• <b>Name des Kunden:</b> {name}", styles["MonoS"]))
    story.append(Paragraph(f"• <b>Lieferadresse (aus SDD):</b> {addr}", styles["MonoS"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Was ist jetzt zu tun", styles["H2"]))
    for line in [
        "1) Anwesenheit an der Adresse bis 24:00; Ausweis bereithalten.",
        "2) Übergabe und Unterschrift bei Erhalt der Karte.",
        "3) Aktivierung mit OTP, das an die Kontakte des Kunden gesendet wird.",
        "4) Mittel vorab gutgeschrieben – unmittelbar nach Aktivierung verfügbar.",
        "5) Überweisung auf Kunden-IBAN per Banktransfer.",
    ]:
        story.append(Paragraph(line, styles["MonoS"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Betriebsbedingungen", styles["H2"]))
    cond = [
        "• <b>Kartenausgabegebühr:</b> 240 € (Produktion + Expresszustellung).",
        "• <b>Erste 5 ausgehende Verfügungen:</b> ohne Kommissionen; danach gemäß Standardtarif.",
        "• <b>Verrechnung der 240 €:</b> Betrag wird mit der ersten Rate verrechnet; "
        "falls die Rate < 240 € ist, wird der Rest mit den folgenden Raten bis zur vollständigen "
        "Verrechnung ausgeglichen (Anpassung erscheint im Tilgungsplan, ohne Erhöhung der Gesamtkosten des Kredits).",
        "• <b>Finanzfluss und Koordinaten:</b> werden von <b>HIGOBI Immobilien GMBH</b> verwaltet; "
        "Zahlungskoordinaten (falls erforderlich) werden ausschließlich von HIGOBI bereitgestellt.",
    ]
    for p in cond:
        story.append(Paragraph(p, styles["MonoS"]))
    story.append(Spacer(1, 6))

    tech = Table([
        [Paragraph(f"Praktik: {case_num}", styles["MonoS"]), Paragraph(f"UMR: {umr}", styles["MonoS"])],
        [Paragraph(f"Adresse (SDD): {addr}", styles["MonoS"]), Paragraph("", styles["MonoS"])],
    ], colWidths=[doc.width*0.62, doc.width*0.38])
    tech.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.3,colors.lightgrey),
        ("BACKGROUND",(0,0),(-1,-1),colors.whitesmoke),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),5), ("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),2),  ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [tech, Spacer(1, 6)]

    story.append(Paragraph("Unterschriften", styles["H2"]))
    sig_head_l = Paragraph("Unterschrift Kunde", styles["MonoS"])
    sig_head_c = Paragraph("Unterschrift Vertreter<br/>Bank", styles["MonoS"])
    sig_head_r = Paragraph("Unterschrift Vertreter<br/>HIGOBI Immobilien GMBH", styles["MonoS"])
    sig_bank = img_box(ASSETS["sign_bank"], 22*mm)
    sig_c2g  = img_box(ASSETS["sign_c2g"],  22*mm)
    SIG_H = 24*mm
    sig_tbl = Table(
        [
            [sig_head_l, sig_head_c, sig_head_r],
            ["", sig_bank or Spacer(1, SIG_H), sig_c2g or Spacer(1, SIG_H)],
            ["", "", ""],
        ],
        colWidths=[doc.width/3.0, doc.width/3.0, doc.width/3.0],
        rowHeights=[9*mm, SIG_H, 6*mm],
        hAlign="CENTER",
    )
    sig_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("VALIGN",(0,1),(-1,1),"BOTTOM"),
        ("BOTTOMPADDING",(0,1),(-1,1),-6),
        ("LINEBELOW",(0,2),(0,2),1.0,colors.black),
        ("LINEBELOW",(1,2),(1,2),1.0,colors.black),
        ("LINEBELOW",(2,2),(2,2),1.0,colors.black),
    ]))
    story.append(sig_tbl)

    # Контактный футер
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Kontakt: {COMPANY['contact']} | E-Mail: {COMPANY['email']} | Web: {COMPANY['web']}",
                           styles["MonoS"]))

    doc.build(story, onFirstPage=draw_border_and_pagenum, onLaterPages=draw_border_and_pagenum)
    buf.seek(0)
    return buf.read()

# ---------- BOT HANDЛERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Выберите действие:", reply_markup=MAIN_KB)

def _ask_country_text():
    return "Под какую страну готовить документ? Ответьте: Германия или Австрия."

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == BTN_CONTRACT:
        context.user_data["flow"] = "contract"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    if t == BTN_SEPA:
        context.user_data["flow"] = "sepa"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    if t == BTN_AML:
        context.user_data["flow"] = "aml"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    if t == BTN_CARD:
        context.user_data["flow"] = "card"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    await update.message.reply_text("Нажмите одну из кнопок.", reply_markup=MAIN_KB)
    return ConversationHandler.END

def _parse_country(txt: str) -> str | None:
    s = (txt or "").strip().lower()
    if s in ("de", "германия", "germany", "deutschland"): return "DE"
    if s in ("at", "австрия", "austria", "österreich", "oesterreich"): return "AT"
    return None

async def ask_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cc = _parse_country(update.message.text)
    if not cc:
        await update.message.reply_text("Пожалуйста, укажите: Германия или Австрия."); return ASK_COUNTRY
    bp = get_bank_profile(cc)
    context.user_data["country"] = cc
    context.user_data["bank_name"] = bp["name"]
    context.user_data["bank_addr"] = bp["addr"]

    flow = context.user_data.get("flow")
    if flow == "contract":
        await update.message.reply_text("Имя клиента (например: Mark Schneider)")
        return ASK_CLIENT
    if flow == "sepa":
        await update.message.reply_text("SEPA-мандат: укажите ФИО/название (как в документе).")
        return SDD_NAME
    if flow == "aml":
        await update.message.reply_text("АМЛ-комиссия: укажите ФИО (Name).")
        return AML_NAME
    if flow == "card":
        await update.message.reply_text("Выдача на карту: укажите ФИО клиента.")
        return CARD_NAME
    await update.message.reply_text("Неизвестный режим. Начните заново /start.")
    return ConversationHandler.END

# --- CONTRACT FSM
async def ask_client(update, context):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Пожалуйста, укажите ФИО клиента."); return ASK_CLIENT
    context.user_data["client"] = name
    await update.message.reply_text("Сумма кредита (например: 12.000,00)")
    return ASK_AMOUNT

async def ask_amount(update, context):
    try:
        amount = parse_num(update.message.text)
        if amount <= 0: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректную сумму (например 12.000,00)"); return ASK_AMOUNT
    context.user_data["amount"] = amount
    await update.message.reply_text("Номинальная ставка Sollzins, % годовых (например 6,45)")
    return ASK_TAN

async def ask_tan(update, context):
    try:
        tan = parse_num(update.message.text)
        if tan < 0 or tan > 50: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный Sollzins, например 6,45"); return ASK_TAN
    context.user_data["tan"] = tan
    await update.message.reply_text("Эффективная ставка Effektiver Jahreszins, % годовых (например 7,98)")
    return ASK_EFF

async def ask_eff(update, context):
    try:
        eff = parse_num(update.message.text)
        if eff < 0 or eff > 60: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный Effektiver Jahreszins, например 7,98"); return ASK_EFF
    context.user_data["eff"] = eff
    await update.message.reply_text("Срок (в месяцах, максимум 84)")
    return ASK_TERM

async def ask_term(update, context):
    try:
        term = int(parse_num(update.message.text))
        if term <= 0 or term > 84: raise ValueError
    except Exception:
        await update.message.reply_text("Введите срок от 1 до 84 месяцев"); return ASK_TERM
    context.user_data["term"] = term

    pdf_bytes = build_contract_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename=f"Vorvertrag_{now_de_date().replace('.','')}.pdf"),
        caption="Готово."
    )
    return ConversationHandler.END

# --- SEPA FSM
async def sdd_name(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите ФИО/название."); return SDD_NAME
    context.user_data["name"] = v; await update.message.reply_text("Адрес (улица/дом)"); return SDD_ADDR

async def sdd_addr(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите адрес."); return SDD_ADDR
    context.user_data["addr"] = v; await update.message.reply_text("PLZ / Город / Земля (в одну строку)."); return SDD_CITY

async def sdd_city(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите PLZ / Город / Землю."); return SDD_CITY
    context.user_data["capcity"] = v; await update.message.reply_text("Страна."); return SDD_COUNTRY

async def sdd_country(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите страну."); return SDD_COUNTRY
    context.user_data["country"] = v; await update.message.reply_text("ID/Steuer-Nr. (если нет — «-»)"); return SDD_ID

async def sdd_id(update, context):
    v = (update.message.text or "").strip()
    context.user_data["idnum"] = "" if v == "-" else v
    await update.message.reply_text("IBAN (без пробелов)"); return SDD_IBAN

async def sdd_iban(update, context):
    iban = (update.message.text or "").replace(" ", "")
    if not iban: await update.message.reply_text("Введите IBAN (без пробелов)."); return SDD_IBAN
    context.user_data["iban"] = iban; await update.message.reply_text("BIC (если нет — «-»)"); return SDD_BIC

async def sdd_bic(update, context):
    bic = (update.message.text or "").strip()
    context.user_data["bic"] = "" if bic == "-" else bic
    pdf_bytes = sepa_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename=f"SEPA_Mandat_{now_de_date().replace('.','')}.pdf"),
        caption="Готово. SEPA-мандат сформирован."
    )
    return ConversationHandler.END

# --- AML FSM
async def aml_name(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите ФИО."); return AML_NAME
    context.user_data["aml_name"] = v; await update.message.reply_text("ID/Steuer-Nr. (если нет — «-»)"); return AML_ID

async def aml_id(update, context):
    v = (update.message.text or "").strip()
    context.user_data["aml_id"] = "" if v == "-" else v
    await update.message.reply_text("IBAN (без пробелов)"); return AML_IBAN

async def aml_iban(update, context):
    iban = (update.message.text or "").replace(" ", "")
    if not iban: await update.message.reply_text("Введите IBAN (без пробелов)."); return AML_IBAN
    context.user_data["aml_iban"] = iban
    pdf_bytes = aml_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Sicherheitszahlung_Anforderung.pdf"),
        caption="Готово. Письмо (АМЛ/комплаенс) сформировано.",
    )
    return ConversationHandler.END

# --- CARD FSM
async def card_name(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите ФИО клиента."); return CARD_NAME
    context.user_data["card_name"] = v; await update.message.reply_text("Адрес доставки (из SDD): улица/дом, PLZ, город, земля."); return CARD_ADDR

async def card_addr(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите адрес доставки полностью."); return CARD_ADDR
    context.user_data["card_addr"] = v
    pdf_bytes = card_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Auszahlung_per_Karte.pdf"),
        caption="Готово. Документ о выдаче на карту сформирован.",
    )
    return ConversationHandler.END

# ---------- BOOTSTRAP ----------
def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Env TELEGRAM_TOKEN is missing")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))

    conv_contract = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_CONTRACT)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            ASK_CLIENT:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_client)],
            ASK_AMOUNT:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_amount)],
            ASK_TAN:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tan)],
            ASK_EFF:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_eff)],
            ASK_TERM:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_term)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    conv_sdd = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_SEPA)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            SDD_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_name)],
            SDD_ADDR:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_addr)],
            SDD_CITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_city)],
            SDD_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_country)],
            SDD_ID:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_id)],
            SDD_IBAN:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_iban)],
            SDD_BIC:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_bic)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    conv_aml = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_AML)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            AML_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, aml_name)],
            AML_ID:[MessageHandler(filters.TEXT & ~filters.COMMAND, aml_id)],
            AML_IBAN:[MessageHandler(filters.TEXT & ~filters.COMMAND, aml_iban)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    conv_card = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_CARD)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            CARD_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, card_name)],
            CARD_ADDR:[MessageHandler(filters.TEXT & ~filters.COMMAND, card_addr)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv_contract)
    app.add_handler(conv_sdd)
    app.add_handler(conv_aml)
    app.add_handler(conv_card)

    logging.info("HIGOBI DE/AT bot started (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
