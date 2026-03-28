import os
import re
import sqlite3
from datetime import datetime
from tkinter import filedialog

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.widgets.tableview import Tableview
from PIL import Image, ImageTk


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "rubrica.db")


class SafeDateEntry(ttk.DateEntry):
    """Workaround readonly DateEntry: abilita temporaneamente durante scelta data."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._restore_state = None

    def _on_date_ask(self):
        self._restore_state = self.entry.cget("state")
        if self._restore_state == "readonly":
            self.entry.configure(state="normal")
        super()._on_date_ask()
        if self._restore_state == "readonly":
            self.entry.configure(state="readonly")


class RubricaApp:
    def __init__(self, root: ttk.Window):
        self.root = root
        self.root.title("📒 Rubrica (Modern)")
        self.root.geometry("1180x720")
        self.root.minsize(1000, 640)

        self.conn = None
        self.cur = None

        self.selected_id = None
        self.foto_path = None
        self._photo_preview = None
        self._search_after_id = None

        # ✅ set degli ID contatto selezionati con checkbox
        self.checked_ids = set()

        # Placeholder per i campi Entry (NON per DateEntry)
        self.placeholders = {
            "search": "Cerca (nome, email, tel, azienda...)",
            "nome": "Es. Moussa",
            "cognome": "Es. Salisou",
            "email": "Es. m.salisou@email.it",
            "tel": "Es. +39 333 1234567",
            "azienda": "Es. Scuola / Azienda",
            "indirizzo": "Es. Via Roma 10, Ferrara",
        }

        self.placeholder_color = "#888888"
        self.normal_color = None
        self._startup_warnings = []

        self._init_db_and_migrate()
        self._build_ui()

        self.root.after(0, self._startup_refresh)

    def _startup_refresh(self):
        self.load_contacts()
        self._debug_db_info("Avvio")
        self.clear_form(show_placeholders=False, clear_search=True)

        if self._startup_warnings:
            Messagebox.show_warning(
                "Vincoli UNICI non applicati:\n\n" + "\n".join(self._startup_warnings),
                "Attenzione"
            )

    # ---------------- NORMALIZZAZIONI ----------------
    def _normalize_email(self, email: str) -> str:
        return (email or "").strip().lower()

    def _normalize_tel(self, tel: str) -> str:
        tel = (tel or "").strip()
        tel = re.sub(r"[^\d+]", "", tel)
        return tel

    # ---------------- DB + MIGRAZIONE + INDICI UNICI ----------------
    def _init_db_and_migrate(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.cur = self.conn.cursor()

        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS contatti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT,
                cognome TEXT,
                email TEXT,
                tel TEXT,
                azienda TEXT,
                indirizzo TEXT,
                nascita TEXT,
                foto TEXT
            )
        """)
        self.conn.commit()

        self.cur.execute("PRAGMA table_info(contatti)")
        existing_cols = {row[1] for row in self.cur.fetchall()}

        wanted_cols = {
            "nome": "TEXT",
            "cognome": "TEXT",
            "email": "TEXT",
            "tel": "TEXT",
            "azienda": "TEXT",
            "indirizzo": "TEXT",
            "nascita": "TEXT",
            "foto": "TEXT",
        }

        altered = False
        for col, coltype in wanted_cols.items():
            if col not in existing_cols:
                self.cur.execute(f"ALTER TABLE contatti ADD COLUMN {col} {coltype}")
                altered = True
        if altered:
            self.conn.commit()

        # unique (solo se valorizzati)
        try:
            self.cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_contatti_email
                ON contatti(email)
                WHERE email IS NOT NULL AND email <> ''
            """)
            self.cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_contatti_tel
                ON contatti(tel)
                WHERE tel IS NOT NULL AND tel <> ''
            """)
            self.conn.commit()
        except sqlite3.IntegrityError:
            self._startup_warnings.append(
                "Nel DB ci sono già email/tel duplicati: elimina/modifica i duplicati e riavvia "
                "per attivare i vincoli UNICI."
            )

    def _debug_db_info(self, where=""):
        try:
            self.cur.execute("SELECT COUNT(*) FROM contatti")
            n = self.cur.fetchone()[0]
        except Exception:
            n = "?"
        msg = f"{where} • DB: {DB_PATH} • righe: {n}"
        self._set_status(msg)
        print("[DEBUG]", msg)

    # ---------------- PLACEHOLDER HELPERS ----------------
    def _set_placeholder(self, key: str):
        ent = self.entries[key]
        var = self.vars[key]
        ph = self.placeholders[key]
        if (var.get() or "").strip() == "":
            var.set(ph)
            ent.configure(foreground=self.placeholder_color)

    def _clear_placeholder(self, key: str):
        ent = self.entries[key]
        var = self.vars[key]
        ph = self.placeholders[key]
        if var.get() == ph:
            var.set("")
            if self.normal_color is None:
                self.normal_color = ent.cget("foreground")
            ent.configure(foreground=self.normal_color)

    def _on_focus_in(self, key: str):
        self._clear_placeholder(key)

    def _on_focus_out(self, key: str):
        self._set_placeholder(key)

    def _value_without_placeholder(self, key: str) -> str:
        v = (self.vars[key].get() or "").strip()
        return "" if v == self.placeholders[key] else v

    # ---------------- UI ----------------
    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="📒 Rubrica", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")

        self.entries = {}
        self.vars = {}

        # Search
        self.search_var = ttk.StringVar(value="")
        self.search_entry = ttk.Entry(header, textvariable=self.search_var, bootstyle="secondary")
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(14, 10))
        self.vars["search"] = self.search_var
        self.entries["search"] = self.search_entry

        self.search_entry.bind("<FocusIn>", lambda e: self._on_focus_in("search"))
        self.search_entry.bind("<FocusOut>", lambda e: self._on_focus_out("search"))
        self.search_entry.bind("<KeyRelease>", self._debounced_search)

        ttk.Button(header, text="Nuovo", bootstyle="primary-outline",
                   command=lambda: self.clear_form(show_placeholders=True, clear_search=False))\
            .grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="Aggiorna", bootstyle="secondary-outline", command=self.load_contacts)\
            .grid(row=0, column=3, padx=(0, 8))
        ttk.Button(header, text="Info DB", bootstyle="info-outline",
                   command=lambda: self._debug_db_info("Info DB"))\
            .grid(row=0, column=4, padx=(0, 8))
        ttk.Button(header, text="Esci", bootstyle="secondary-outline", command=self._quit)\
            .grid(row=0, column=5)

        main = ttk.Frame(self.root, padding=(16, 0, 16, 16))
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=0)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self.form_card = ttk.Labelframe(main, text="Dettagli contatto", padding=14, bootstyle="secondary")
        self.form_card.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        self.form_card.columnconfigure(0, weight=1)

        self.table_card = ttk.Labelframe(main, text="Dati (Griglia)", padding=12, bootstyle="secondary")
        self.table_card.grid(row=0, column=1, sticky="nsew")
        self.table_card.columnconfigure(0, weight=1)
        self.table_card.rowconfigure(2, weight=1)

        topbar = ttk.Frame(self.table_card)
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        topbar.columnconfigure(0, weight=1)

        self.status = ttk.Label(topbar, text="Pronto", bootstyle="secondary")
        self.status.grid(row=0, column=0, sticky="w")

        # ✅ Barra azioni checkbox
        checkbar = ttk.Frame(self.table_card)
        checkbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        checkbar.columnconfigure(0, weight=1)

        ttk.Button(checkbar, text="☑ Seleziona tutti", bootstyle="secondary-outline",
                   command=self.check_all).grid(row=0, column=0, sticky="w")
        ttk.Button(checkbar, text="☐ Deseleziona tutti", bootstyle="secondary-outline",
                   command=self.uncheck_all).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(checkbar, text="🗑 Elimina selezionati (☑)", bootstyle="danger-outline",
                   command=self.delete_checked).grid(row=0, column=2, sticky="w", padx=8)

        self.fields = {}
        r = 0

        def add_field(label, key):
            nonlocal r
            ttk.Label(self.form_card, text=label).grid(row=r, column=0, sticky="w", pady=(0, 4))
            var = ttk.StringVar(value="")
            ent = ttk.Entry(self.form_card, textvariable=var, bootstyle="secondary")
            ent.grid(row=r + 1, column=0, sticky="ew", pady=(0, 10))

            self.vars[key] = var
            self.entries[key] = ent

            ent.bind("<FocusIn>", lambda e, k=key: self._on_focus_in(k))
            ent.bind("<FocusOut>", lambda e, k=key: self._on_focus_out(k))

            self.fields[key] = var
            r += 2

        add_field("Nome *", "nome")
        add_field("Cognome", "cognome")
        add_field("Email (unica)", "email")
        add_field("Telefono (unico)", "tel")
        add_field("Azienda", "azienda")
        add_field("Indirizzo", "indirizzo")

        ttk.Label(self.form_card, text="Nascita (opzionale)").grid(row=r, column=0, sticky="w", pady=(0, 4))
        self.nascita = SafeDateEntry(self.form_card, bootstyle="secondary", dateformat="%Y-%m-%d")
        self.nascita.grid(row=r + 1, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(self.form_card, text="Svuota data", bootstyle="secondary-outline", command=self.clear_date)\
            .grid(row=r + 2, column=0, sticky="ew", pady=(0, 12))
        r += 3

        photo_box = ttk.Frame(self.form_card)
        photo_box.grid(row=r, column=0, sticky="ew", pady=(6, 10))
        photo_box.columnconfigure(1, weight=1)

        self.photo_label = ttk.Label(photo_box, text="Nessuna foto", anchor="center", width=18)
        self.photo_label.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 10))

        ttk.Button(photo_box, text="Scegli foto", bootstyle="info-outline", command=self.choose_photo)\
            .grid(row=0, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(photo_box, text="Rimuovi foto", bootstyle="secondary-outline", command=self.remove_photo)\
            .grid(row=1, column=1, sticky="ew")

        actions = ttk.Frame(self.form_card)
        actions.grid(row=r + 2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(actions, text="➕ Aggiungi", bootstyle="success", command=self.add_contact)\
            .grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="✏️ Modifica", bootstyle="warning", command=self.update_contact)\
            .grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(actions, text="🗑 Elimina", bootstyle="danger", command=self.delete_contact)\
            .grid(row=0, column=2, sticky="ew", padx=(8, 0))

        colors = self.root.style.colors

        # ✅ Aggiunta colonna checkbox come prima colonna
        self.coldata = [
            {"text": "☑", "stretch": False, "width": 45, "anchor": "center"},  # checkbox
            {"text": "ID", "stretch": False, "width": 70, "anchor": "center"},
            {"text": "Nome", "stretch": True, "width": 140, "anchor": "w"},
            {"text": "Cognome", "stretch": True, "width": 140, "anchor": "w"},
            {"text": "Email", "stretch": True, "width": 240, "anchor": "w"},
            {"text": "Telefono", "stretch": False, "width": 140, "anchor": "center"},
            {"text": "Azienda", "stretch": True, "width": 160, "anchor": "w"},
            {"text": "Indirizzo", "stretch": True, "width": 260, "anchor": "w"},
            {"text": "Nascita", "stretch": False, "width": 120, "anchor": "center"},
        ]

        self.table = Tableview(
            master=self.table_card,
            coldata=self.coldata,
            rowdata=[],
            paginated=False,
            searchable=False,
            autofit=True,
            autoalign=True,
            bootstyle=PRIMARY,
            stripecolor=(colors.light, None),
            height=18,
        )
        self.table.grid(row=2, column=0, sticky="nsew")

        # ✅ Eventi sulla Treeview interna (Tableview.view) [1](https://ttkbootstrap.readthedocs.io/en/latest/api/tableview/tableview/)[2](https://stackoverflow.com/questions/77849171/unable-to-bindtreeviewselect-to-work-with-tableview-to-get-selected-ro)
        self.table.view.bind("<<TreeviewSelect>>", self.on_select_table)
        self.table.view.bind("<Button-1>", self.on_table_click, add=True)

    # ---------------- Checkbox logic ----------------
    def on_table_click(self, event):
        """Toggle checkbox se clicchi sulla prima colonna."""
        tv = self.table.view
        row_iid = tv.identify_row(event.y)
        col = tv.identify_column(event.x)  # "#1" è la prima colonna visibile

        if not row_iid:
            return

        # prima colonna = checkbox
        if col == "#1":
            values = list(tv.item(row_iid, "values"))
            if not values or len(values) < 2:
                return "break"

            try:
                contact_id = int(values[1])  # colonna ID è la seconda (index 1)
            except Exception:
                return "break"

            if contact_id in self.checked_ids:
                self.checked_ids.remove(contact_id)
                values[0] = "☐"
            else:
                self.checked_ids.add(contact_id)
                values[0] = "☑"

            tv.item(row_iid, values=values)
            self._set_status(f"Selezionati (☑): {len(self.checked_ids)}")
            return "break"  # evita che il click sposti la selezione riga

    def check_all(self):
        """Seleziona tutte le righe visibili in tabella."""
        tv = self.table.view
        for iid in tv.get_children():
            vals = list(tv.item(iid, "values"))
            if len(vals) >= 2:
                try:
                    cid = int(vals[1])
                    self.checked_ids.add(cid)
                    vals[0] = "☑"
                    tv.item(iid, values=vals)
                except Exception:
                    pass
        self._set_status(f"Selezionati (☑): {len(self.checked_ids)}")

    def uncheck_all(self):
        """Deseleziona tutte le righe."""
        self.checked_ids.clear()
        tv = self.table.view
        for iid in tv.get_children():
            vals = list(tv.item(iid, "values"))
            if vals:
                vals[0] = "☐"
                tv.item(iid, values=vals)
        self._set_status("Selezionati (☑): 0")

    def delete_checked(self):
        """Elimina dal DB tutti i contatti spuntati."""
        if not self.checked_ids:
            Messagebox.show_info("Nessun contatto selezionato (☑).", "Info")
            return

        if not Messagebox.okcancel(
            f"Eliminare {len(self.checked_ids)} contatti selezionati (☑)?",
            "Conferma",
            alert=True
        ):
            return

        try:
            self.cur.executemany("DELETE FROM contatti WHERE id=?", [(i,) for i in self.checked_ids])
            self.conn.commit()
        except Exception as e:
            Messagebox.show_error(f"Errore eliminazione multipla:\n{e}", "DB")
            return

        self.checked_ids.clear()
        self.clear_form(show_placeholders=True, clear_search=False)
        self.load_contacts()
        self._debug_db_info("Eliminati (batch) 🗑")

    # ---------------- Helpers ----------------
    def _set_status(self, text: str):
        self.status.config(text=text)

    def _debounced_search(self, _event=None):
        if self._search_after_id:
            self.root.after_cancel(self._search_after_id)
        self._search_after_id = self.root.after(200, self.load_contacts)

    def _quit(self):
        try:
            if self.conn:
                self.conn.commit()
                self.conn.close()
        except Exception:
            pass
        self.root.destroy()

    # ---------------- Date helpers ----------------
    def clear_date(self):
        self.nascita.entry.delete(0, END)

    # ---------------- Foto ----------------
    def choose_photo(self):
        path = filedialog.askopenfilename(filetypes=[("Immagini", "*.png *.jpg *.jpeg")])
        if not path:
            return
        self.foto_path = path
        self._update_photo_preview(path)
        self._set_status("Foto selezionata")

    def remove_photo(self):
        self.foto_path = None
        self._photo_preview = None
        self.photo_label.config(image="", text="Nessuna foto")

    def _update_photo_preview(self, path: str):
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((140, 140))
            self._photo_preview = ImageTk.PhotoImage(img)
            self.photo_label.config(image=self._photo_preview, text="")
        except Exception:
            self.photo_label.config(text="Errore foto", image="")
            self._photo_preview = None

    # ---------------- Form ----------------
    def clear_form(self, show_placeholders=True, clear_search=False):
        self.selected_id = None

        for key, var in self.fields.items():
            var.set("")
            if self.normal_color is None:
                self.normal_color = self.entries[key].cget("foreground")
            self.entries[key].configure(foreground=self.normal_color)
            if show_placeholders:
                self._set_placeholder(key)

        self.clear_date()
        self.remove_photo()

        if clear_search:
            self.search_var.set("")
            if show_placeholders:
                self._set_placeholder("search")

        self._set_status("Nuovo contatto")

    def _get_form_data(self):
        nome = self._value_without_placeholder("nome")
        cognome = self._value_without_placeholder("cognome")
        email = self._normalize_email(self._value_without_placeholder("email"))
        tel = self._normalize_tel(self._value_without_placeholder("tel"))
        azienda = self._value_without_placeholder("azienda")
        indirizzo = self._value_without_placeholder("indirizzo")

        nascita = (self.nascita.entry.get() or "").strip()
        if nascita:
            try:
                datetime.strptime(nascita, "%Y-%m-%d")
            except ValueError:
                Messagebox.show_warning("Formato nascita non valido. Usa YYYY-MM-DD.", "Attenzione")
                nascita = ""

        email = email if email else None
        tel = tel if tel else None
        return nome, cognome, email, tel, azienda, indirizzo, nascita

    # ---------------- Unicità (check app) ----------------
    def _check_unique(self, email, tel, exclude_id=None):
        if email:
            if exclude_id:
                self.cur.execute("SELECT id FROM contatti WHERE email=? AND id<>?", (email, exclude_id))
            else:
                self.cur.execute("SELECT id FROM contatti WHERE email=?", (email,))
            if self.cur.fetchone():
                return "Email già presente: deve essere unica."

        if tel:
            if exclude_id:
                self.cur.execute("SELECT id FROM contatti WHERE tel=? AND id<>?", (tel, exclude_id))
            else:
                self.cur.execute("SELECT id FROM contatti WHERE tel=?", (tel,))
            if self.cur.fetchone():
                return "Telefono già presente: deve essere unico."

        return None

    # ---------------- CRUD ----------------
    def load_contacts(self):
        try:
            filtro = (self.search_var.get() or "").strip().lower()
            if filtro == self.placeholders["search"].lower():
                filtro = ""

            self.cur.execute("""
                SELECT id, nome, cognome, email, tel, azienda, indirizzo, nascita
                FROM contatti
                ORDER BY nome, cognome
            """)
            rows = self.cur.fetchall()

            data = []
            for r in rows:
                r = [x if x is not None else "" for x in r]
                if not filtro or filtro in " ".join(map(str, r)).lower():
                    cid = int(r[0])
                    check = "☑" if cid in self.checked_ids else "☐"
                    data.append([check] + r)  # aggiunge colonna checkbox davanti

            self.table.build_table_data(coldata=self.coldata, rowdata=data)
            self.table.reset_table()
            self.root.update_idletasks()
            self._set_status(f"Mostrati {len(data)} contatti • Selezionati (☑): {len(self.checked_ids)}")

        except Exception as e:
            Messagebox.show_error(f"Errore griglia:\n{e}", "Errore")
            print("[ERROR load_contacts]", e)

    def add_contact(self):
        nome, cognome, email, tel, azienda, indirizzo, nascita = self._get_form_data()
        if not nome:
            Messagebox.show_warning("Il nome è obbligatorio.", "Errore")
            return

        msg = self._check_unique(email, tel, exclude_id=None)
        if msg:
            Messagebox.show_warning(msg, "Duplicato")
            return

        try:
            self.cur.execute("""
                INSERT INTO contatti
                (nome, cognome, email, tel, azienda, indirizzo, nascita, foto)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (nome, cognome, email, tel, azienda, indirizzo, nascita, self.foto_path))
            self.conn.commit()
        except sqlite3.IntegrityError:
            Messagebox.show_error("Email o Telefono duplicato. Devono essere unici.", "DB")
            return
        except Exception as e:
            Messagebox.show_error(f"Errore salvataggio:\n{e}", "DB")
            return

        self.clear_form(show_placeholders=True, clear_search=False)
        self.load_contacts()
        self._debug_db_info("Inserito ✅")

    def update_contact(self):
        if not self.selected_id:
            Messagebox.show_info("Seleziona un contatto dalla tabella.", "Info")
            return

        nome, cognome, email, tel, azienda, indirizzo, nascita = self._get_form_data()
        if not nome:
            Messagebox.show_warning("Il nome è obbligatorio.", "Errore")
            return

        msg = self._check_unique(email, tel, exclude_id=self.selected_id)
        if msg:
            Messagebox.show_warning(msg, "Duplicato")
            return

        try:
            self.cur.execute("""
                UPDATE contatti SET
                  nome=?, cognome=?, email=?, tel=?, azienda=?, indirizzo=?, nascita=?, foto=?
                WHERE id=?
            """, (nome, cognome, email, tel, azienda, indirizzo, nascita, self.foto_path, self.selected_id))
            self.conn.commit()
        except sqlite3.IntegrityError:
            Messagebox.show_error("Email o Telefono duplicato. Devono essere unici.", "DB")
            return
        except Exception as e:
            Messagebox.show_error(f"Errore modifica:\n{e}", "DB")
            return

        self.clear_form(show_placeholders=True, clear_search=False)
        self.load_contacts()
        self._debug_db_info("Aggiornato ✨")

    def delete_contact(self):
        sel = self.table.view.selection()
        if not sel:
            Messagebox.show_info("Seleziona un contatto dalla tabella.", "Info")
            return

        values = self.table.view.item(sel[0])["values"]
        # con colonna checkbox davanti, l'ID è values[1]
        id_cont = values[1]
        label = f"{values[2]} {values[3]}".strip()

        if not Messagebox.okcancel(f"Eliminare '{label}'?", "Conferma", alert=True):
            return

        try:
            self.cur.execute("DELETE FROM contatti WHERE id=?", (id_cont,))
            self.conn.commit()
        except Exception as e:
            Messagebox.show_error(f"Errore eliminazione:\n{e}", "DB")
            return

        # se era spuntato, toglilo
        try:
            cid = int(id_cont)
            self.checked_ids.discard(cid)
        except Exception:
            pass

        self.clear_form(show_placeholders=True, clear_search=False)
        self.load_contacts()
        self._debug_db_info("Eliminato 🗑")

    def on_select_table(self, _event=None):
        sel = self.table.view.selection()
        if not sel:
            return

        values = self.table.view.item(sel[0])["values"]
        # values: [check, id, nome, cognome, email, tel, azienda, indirizzo, nascita]
        self.selected_id = int(values[1])

        self.fields["nome"].set(values[2] or "")
        self.fields["cognome"].set(values[3] or "")
        self.fields["email"].set(values[4] or "")
        self.fields["tel"].set(values[5] or "")
        self.fields["azienda"].set(values[6] or "")
        self.fields["indirizzo"].set(values[7] or "")

        self.nascita.entry.delete(0, END)
        if values[8]:
            self.nascita.entry.insert(0, values[8])

        self.cur.execute("SELECT foto FROM contatti WHERE id=?", (self.selected_id,))
        row = self.cur.fetchone()
        foto = row[0] if row else None

        if foto:
            self.foto_path = foto
            self._update_photo_preview(foto)
        else:
            self.remove_photo()

        self._set_status(f"Selezionato ID {self.selected_id}")


if __name__ == "__main__":
    app = ttk.Window(themename="superhero")
    RubricaApp(app)
    app.mainloop()