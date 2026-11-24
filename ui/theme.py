"""Three-colour theme.  Primary: navy.  Secondary: warm white.  Accent: teal.
(Other values are only lighter/darker shades of these three.)  Base text is 13 pt, about +4 pt over the usual 9 pt."""
NAVY = "#12283f"          # primary
NAVY_DARK = "#0b1b2c"
NAVY_LIGHT = "#244a70"
PAPER = "#f6f3ec"         # secondary
CARD = "#fffdf9"
LINE = "#d9d2c2"
TEAL = "#0b7a75"          # accent
TEAL_DARK = "#085f5b"
MUTED = "#4a5b6b"

BASE_PT = 13

STYLE = f"""
* {{ font-size: {BASE_PT}pt; }}
QWidget {{ color: {NAVY}; }}
QMainWindow, QDialog, QMessageBox, QWidget#page {{ background: {PAPER}; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 24pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 18pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}

QFrame#sidebar {{ background: {NAVY}; }}
QFrame#sidebar QLabel {{ color: white; }}
QFrame#sidebar QLabel#brand {{ font-size: 20pt; font-weight: 700; }}
QFrame#sidebar QLabel#sidemuted {{ color: #d3dde8; }}
QFrame#sidebar QProgressBar {{ background: {NAVY_LIGHT}; border: none; height: 8px; border-radius: 4px; }}
QFrame#sidebar QProgressBar::chunk {{ background: {TEAL}; border-radius: 4px; }}

QPushButton {{ background: {TEAL}; color: white; border: 2px solid {TEAL}; border-radius: 8px;
              padding: 9px 22px; font-weight: 600; }}
QPushButton:hover {{ background: {TEAL_DARK}; border-color: {TEAL_DARK}; }}
QPushButton:pressed {{ background: {NAVY}; border-color: {NAVY}; }}
QPushButton:disabled {{ background: #d5dbe1; border-color: #d5dbe1; color: #3b4957; }}

QPushButton[kind="secondary"] {{ background: white; color: {NAVY}; border: 2px solid {NAVY}; }}
QPushButton[kind="secondary"]:hover {{ background: #e6ecf2; }}
QPushButton[kind="secondary"]:disabled {{ background: #eceff2; color: #3b4957; border-color: #9aa8b5; }}

QPushButton[kind="nav"] {{ background: transparent; color: white; border: none; text-align: left;
                          padding: 13px 18px; border-radius: 8px; font-size: 14pt; }}
QPushButton[kind="nav"]:hover {{ background: {NAVY_LIGHT}; }}
QPushButton[kind="nav"]:checked {{ background: {TEAL}; }}
QPushButton[kind="side"] {{ background: transparent; color: white; border: 2px solid #6f88a3; }}
QPushButton[kind="side"]:hover {{ background: {NAVY_LIGHT}; }}

QLineEdit {{ background: white; color: {NAVY}; border: 2px solid {LINE}; border-radius: 10px; padding: 8px 14px;
            selection-background-color: {TEAL}; selection-color: white; }}
QLineEdit:focus {{ border-color: {TEAL}; }}
QLineEdit#search {{ font-size: 17pt; padding: 12px 16px; }}

QSpinBox {{ background: white; color: {NAVY}; border: 2px solid {LINE}; border-radius: 8px; padding: 5px 8px; }}

QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#card QLabel#name {{ font-size: 15pt; font-weight: 700; }}

QTableWidget {{ background: white; border: 1px solid {LINE}; border-radius: 8px; gridline-color: {LINE};
               selection-background-color: {TEAL}; selection-color: white; }}
QTableWidget::item {{ padding: 6px; }}
QHeaderView::section {{ background: {NAVY}; color: white; padding: 10px; border: none; font-weight: 600; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {PAPER}; width: 14px; }}
QScrollBar::handle:vertical {{ background: #aab6c2; border-radius: 6px; min-height: 30px; }}
QToolTip {{ background: {NAVY}; color: white; border: none; padding: 6px; }}
QPrintDialog, QFileDialog {{ background: {PAPER}; }}
"""
