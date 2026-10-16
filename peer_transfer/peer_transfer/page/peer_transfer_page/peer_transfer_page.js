frappe.provide("peer_transfer");

frappe.pages["peer-transfer-page"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Peer Transfer"),
		single_column: true,
	});
	frappe.breadcrumbs.add({
		type: "Custom",
		label: __("Peer Transfer"),
		route: "peer-transfer-page",
	});
	new peer_transfer.PeerTransferPage({ container: wrapper });
};

peer_transfer.PeerTransferPage = class PeerTransferPage {
	constructor({ container }) {
		this.$container = $(container);
		this.selected = []; // [{name, label}] customers picked on SEND
		this.search_timer = null;
		this.offer = null; // {code, name, counts} from create_offer
		this.conn = null; // {name, manifest, schema_diff} from connect_with_code
		this.pv = { key: null, offset: 0, limit: 50, total: 0, rows: [] };
		this.busy = false;
		if (typeof window !== "undefined") window.__PT = this;

		this.render();
		this.inject_styles();
		this.bind_tabs();
		this.show_tab("send");
		this.load_log();
	}

	// ---------------------------------------------------------------- styles

	inject_styles() {
		if (document.getElementById("pt-page-styles")) return;
		$("head").append(`
			<style id="pt-page-styles">
				.pt-wrap { max-width: 1100px; margin: 0 auto; }
				.pt-tabs { display: flex; gap: 6px; margin: 4px 0 12px; }
				.pt-tab { border: 1px solid var(--border-color); background: var(--fg-color);
					border-radius: 6px 6px 0 0; padding: 7px 18px; font-size: 13px; font-weight: 600;
					cursor: pointer; color: var(--text-muted); }
				.pt-tab.active { color: var(--text-color); border-bottom-color: var(--fg-color);
					background: var(--gray-50); }
				.pt-card { border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; margin-top: 12px; }
				.pt-card-head { padding: 10px 14px; border-bottom: 1px solid var(--border-color);
					background: var(--gray-50); font-weight: 600; font-size: 13px; }
				.pt-card-body { padding: 12px 14px; font-size: 13px; }
				.pt-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 10px; }
				.pt-field { display: flex; flex-direction: column; gap: 4px; min-width: 180px; flex: 1; }
				.pt-field label { font-size: 12px; color: var(--text-muted); font-weight: 600; }
				.pt-code-big { font-family: monospace; font-size: 30px; font-weight: 700;
					letter-spacing: 2px; padding: 12px 16px; background: var(--gray-50);
					border: 1px dashed var(--border-color); border-radius: 8px;
					display: inline-block; user-select: all; }
				.pt-chip { display: inline-flex; align-items: center; gap: 6px; background: var(--gray-100);
					border: 1px solid var(--border-color); border-radius: 14px; padding: 2px 6px 2px 10px;
					font-size: 12px; margin: 0 6px 6px 0; }
				.pt-chip button { border: none; background: transparent; cursor: pointer; color: var(--red-600); font-size: 13px; }
				.pt-search-results { border: 1px solid var(--border-color); border-radius: 6px; max-height: 220px;
					overflow: auto; margin-top: 4px; display: none; }
				.pt-search-results.open { display: block; }
				.pt-search-item { padding: 6px 10px; cursor: pointer; font-size: 13px; }
				.pt-search-item:hover, .pt-search-item.active { background: var(--gray-100); }
				.pt-pill { border-radius: 10px; padding: 2px 11px; font-size: 12px; font-weight: 600; }
				.pt-pill-green { background: var(--green-100); color: var(--green-700); }
				.pt-pill-red { background: var(--red-100); color: var(--red-700); }
				.pt-pill-gray { background: var(--gray-200); color: var(--gray-700); }
				.pt-pill-blue { background: var(--blue-100); color: var(--blue-700); }
				.pt-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
				.pt-table th, .pt-table td { border-bottom: 1px solid var(--border-color);
					padding: 5px 8px; text-align: left; vertical-align: top; }
				.pt-table tr.pt-diff-row td { background: var(--red-50); color: var(--red-700); }
				.pt-cust-card { border: 1px solid var(--border-color); border-radius: 8px; margin-top: 10px; overflow: hidden; }
				.pt-cust-head { padding: 8px 12px; background: var(--blue-50); font-weight: 700; font-size: 13px; }
				.pt-cust-sec { padding: 8px 12px; border-top: 1px dashed var(--border-color); }
				.pt-cust-sec-title { font-size: 12px; font-weight: 700; color: var(--text-muted);
					text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 4px; }
				.pt-badge { background: var(--blue-100); color: var(--blue-700); border-radius: 9px;
					padding: 0 8px; font-size: 11px; font-weight: 700; margin-left: 6px; white-space: nowrap; }
				.pt-log-row { cursor: pointer; }
				.pt-log-row:hover td { background: var(--gray-50); }
				.pt-pager { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
				.pt-banner { border-radius: 6px; padding: 9px 12px; margin-top: 8px; font-size: 12.5px; line-height: 1.5; }
				.pt-banner-red { background: var(--red-50); border: 1px solid var(--red-300); color: var(--red-700); }
				.pt-banner-amber { background: #fff8e6; border: 1px solid #e8c547; color: #7a5b00; }
				.pt-banner-green { background: var(--green-50); border: 1px solid var(--green-300); color: var(--green-700); }
				.pt-banner-title { font-weight: 700; display: block; margin-bottom: 4px; }
				.pt-warn-details { margin-top: 8px; }
				.pt-warn-details summary { cursor: pointer; font-weight: 600; }
			</style>
		`);
	}

	// ---------------------------------------------------------------- markup

	render() {
		this.$container.html(`
			<div class="pt-wrap" style="padding: 0 12px 24px;">
				<div class="pt-tabs">
					<button class="btn btn-sm pt-tab" data-tab="send">${__("Send")}</button>
					<button class="btn btn-sm pt-tab" data-tab="receive">${__("Receive")}</button>
					<button class="btn btn-sm pt-tab" data-tab="log">${__("Log")}</button>
				</div>

				<div id="pt-tab-send">
					<div class="pt-card">
						<div class="pt-card-head"><i class="fa fa-upload"></i> ${__("Send Customers")}</div>
						<div class="pt-card-body">
							<div class="pt-row">
								<div class="pt-field" style="max-width: 220px;">
									<label>${__("Root Doctype")}</label>
									<select id="pt-root-doctype" class="form-control input-sm" disabled>
										<option value="Customer" selected>Customer</option>
									</select>
								</div>
								<div class="pt-field" style="max-width: 220px;">
									<label>${__("Code TTL")}</label>
									<select id="pt-ttl" class="form-control input-sm">
										<option value="3600" selected>${__("1 hour")}</option>
										<option value="7200">${__("2 hours")}</option>
										<option value="21600">${__("6 hours")}</option>
										<option value="86400">${__("24 hours")}</option>
									</select>
								</div>
							</div>
							<div class="pt-field" style="margin-bottom: 10px;">
								<label>${__("Customers (pick one or more)")}</label>
								<input type="text" id="pt-cust-search" class="form-control input-sm"
									placeholder="${__("Type to search customers...")}" autocomplete="off">
								<div id="pt-cust-results" class="pt-search-results"></div>
							</div>
							<div id="pt-cust-picked" style="margin-bottom: 6px;"></div>
							<div>
								<button class="btn btn-sm btn-primary" id="pt-generate">
									<i class="fa fa-key"></i> ${__("Generate Code")}
								</button>
								<span id="pt-send-status" class="text-muted" style="margin-left: 8px;"></span>
							</div>
						</div>
					</div>
					<div class="pt-card" id="pt-offer-card" style="display: none;">
						<div class="pt-card-head"><i class="fa fa-ticket"></i> ${__("Offer Code")}</div>
						<div class="pt-card-body">
							<div><span class="pt-code-big" id="pt-offer-code"></span></div>
							<div style="margin-top: 8px;">
								<button class="btn btn-sm btn-secondary" id="pt-copy-code">
									<i class="fa fa-copy"></i> ${__("Copy")}
								</button>
							</div>
							<div id="pt-offer-counts" class="text-muted" style="margin-top: 8px;"></div>
						</div>
					</div>
				</div>

				<div id="pt-tab-receive" style="display: none;">
					<div class="pt-card">
						<div class="pt-card-head"><i class="fa fa-download"></i> ${__("Receive")}</div>
						<div class="pt-card-body">
							<div class="pt-row">
								<div class="pt-field">
									<label>${__("Offer Code")}</label>
									<input type="text" id="pt-code" class="form-control input-sm"
										placeholder="${__("Paste the offer code...")}" autocomplete="off"
										style="font-family: monospace;">
								</div>
								<div>
									<button class="btn btn-sm btn-primary" id="pt-connect">
										<i class="fa fa-plug"></i> ${__("Connect")}
									</button>
								</div>
							</div>
							<div id="pt-conn-status" class="text-muted"></div>
						</div>
					</div>
					<div id="pt-schema-panel"></div>
					<div class="pt-card" id="pt-preview-card" style="display: none;">
						<div class="pt-card-head"><i class="fa fa-eye"></i> ${__("Preview")}</div>
						<div class="pt-card-body" id="pt-preview-body"></div>
					</div>
					<div class="pt-card" id="pt-decision-card" style="display: none;">
						<div class="pt-card-head"><i class="fa fa-gavel"></i> ${__("Decision")}</div>
						<div class="pt-card-body">
							<button class="btn btn-sm btn-primary" id="pt-accept">
								<i class="fa fa-check"></i> ${__("Accept & Import")}
							</button>
							<button class="btn btn-sm btn-danger" id="pt-reject" style="margin-left: 8px;">
								<i class="fa fa-times"></i> ${__("Reject")}
							</button>
							<span id="pt-decision-status" class="text-muted" style="margin-left: 8px;"></span>
							<div id="pt-receipt" style="margin-top: 8px;"></div>
						</div>
					</div>
				</div>

				<div id="pt-tab-log" style="display: none;">
					<div class="pt-card">
						<div class="pt-card-head"><i class="fa fa-list"></i> ${__("Recent Transfers")}
							<button class="btn btn-xs btn-secondary pull-right" id="pt-log-reload">
								<i class="fa fa-refresh"></i> ${__("Reload")}
							</button>
						</div>
						<div class="pt-card-body" id="pt-log-body">${__("Loading...")}</div>
					</div>
				</div>
			</div>
		`);
	}

	// ------------------------------------------------------------------ tabs

	bind_tabs() {
		const self = this;
		this.$container.find(".pt-tab").on("click", function () {
			self.show_tab($(this).data("tab"));
		});

		// SEND
		this.$("#pt-cust-search").on("input", () => this.on_customer_search());
		this.$("#pt-cust-search").on("keydown", (e) => this.on_search_key(e));
		this.$("#pt-generate").on("click", () => this.generate_code());
		this.$("#pt-copy-code").on("click", () => this.copy_code());

		// RECEIVE
		this.$("#pt-connect").on("click", () => this.connect());
		this.$("#pt-code").on("keydown", (e) => {
			if (e.key === "Enter") this.connect();
		});
		this.$("#pt-accept").on("click", () => this.accept());
		this.$("#pt-reject").on("click", () => this.reject());

		// LOG
		this.$("#pt-log-reload").on("click", () => this.load_log());

		// Close search dropdown on outside click.
		$(document).on("click.pt-search", (e) => {
			if (!$(e.target).closest("#pt-cust-search, #pt-cust-results").length) {
				this.$("#pt-cust-results").removeClass("open").empty();
			}
		});
	}

	show_tab(name) {
		["send", "receive", "log"].forEach((t) => {
			this.$(`#pt-tab-${t}`).toggle(t === name);
			this.$container
				.find(`.pt-tab[data-tab="${t}"]`)
				.toggleClass("active", t === name);
		});
		if (name === "log") this.load_log();
	}

	$(sel) {
		return this.$container.find(sel);
	}

	esc(value) {
		return frappe.utils.escape_html(value == null ? "" : String(value));
	}

	fail(title, message) {
		frappe.msgprint({ title: title, message: message, indicator: "red" });
	}

	// ------------------------------------------------------------------ SEND

	on_customer_search() {
		clearTimeout(this.search_timer);
		const q = this.$("#pt-cust-search").val().trim();
		if (!q) {
			this.$("#pt-cust-results").removeClass("open").empty();
			return;
		}
		this.search_timer = setTimeout(() => this.run_customer_search(q), 300);
	}

	on_search_key(e) {
		if (e.key === "Enter") {
			const $first = this.$("#pt-cust-results .pt-search-item").first();
			if ($first.length) $first.trigger("click");
		} else if (e.key === "Escape") {
			this.$("#pt-cust-results").removeClass("open").empty();
		}
	}

	run_customer_search(q) {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Customer",
				fields: ["name", "customer_name"],
				or_filters: [
					["Customer", "name", "like", `%${q}%`],
					["Customer", "customer_name", "like", `%${q}%`],
				],
				limit_page_length: 20,
				order_by: "modified desc",
			},
			callback: (r) => this.render_search_results(r.message || []),
			error: (err) =>
				this.fail(__("Customer search failed"), this.esc(err.message || err)),
		});
	}

	render_search_results(rows) {
		const picked = new Set(this.selected.map((s) => s.name));
		const items = rows.filter((c) => !picked.has(c.name));
		if (!items.length) {
			this.$("#pt-cust-results")
				.addClass("open")
				.html(`<div class="pt-search-item text-muted">${__("No matches")}</div>`);
			return;
		}
		this.$("#pt-cust-results")
			.addClass("open")
			.html(
				items
					.map(
						(c) => `<div class="pt-search-item" data-name="${this.esc(c.name)}">
							<strong>${this.esc(c.name)}</strong>
							<span class="text-muted"> · ${this.esc(c.customer_name || "")}</span>
						</div>`
					)
					.join("")
			);
		this.$("#pt-cust-results .pt-search-item[data-name]").on("click", (e) => {
			const $el = $(e.currentTarget);
			this.add_customer($el.data("name"), $el.find("span").text().replace(/^ · /, ""));
			this.$("#pt-cust-results").removeClass("open").empty();
			this.$("#pt-cust-search").val("").focus();
		});
	}

	add_customer(name, label) {
		if (this.selected.some((s) => s.name === name)) return;
		this.selected.push({ name: name, label: label || name });
		this.render_picked();
	}

	remove_customer(name) {
		this.selected = this.selected.filter((s) => s.name !== name);
		this.render_picked();
	}

	render_picked() {
		this.$("#pt-cust-picked").html(
			this.selected.length
				? this.selected
						.map(
							(s) => `<span class="pt-chip">${this.esc(s.name)}
								<button data-unpick="${this.esc(s.name)}" title="${__("Remove")}">×</button>
							</span>`
						)
						.join("")
				: `<span class="text-muted">${__("No customers selected.")}</span>`
		);
		this.$("#pt-cust-picked [data-unpick]").on("click", (e) => {
			this.remove_customer($(e.currentTarget).data("unpick"));
		});
	}

	generate_code() {
		if (this.busy) return;
		if (!this.selected.length) {
			frappe.show_alert({ message: __("Select at least one customer."), indicator: "orange" });
			return;
		}
		this.busy = true;
		this.$("#pt-send-status").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Working...")}`);
		frappe.call({
			method: "peer_transfer.api.transfer.create_offer",
			args: {
				root_doctype: "Customer",
				root_names: this.selected.map((s) => s.name),
				ttl_secs: parseInt(this.$("#pt-ttl").val(), 10) || 3600,
			},
			callback: (r) => {
				this.busy = false;
				this.$("#pt-send-status").html("");
				if (!r.message || !r.message.code) {
					this.fail(__("Generate failed"), __("Empty response from create_offer."));
					return;
				}
				this.offer = r.message;
				this.render_offer();
			},
			error: (err) => {
				this.busy = false;
				this.$("#pt-send-status").html("");
				this.fail(__("Generate failed"), this.esc(err.message || err));
			},
		});
	}

	render_offer() {
		const o = this.offer;
		this.$("#pt-offer-card").show();
		this.$("#pt-offer-code").text(o.code);
		const counts = o.counts || {};
		const parts = Object.entries(counts).map(
			([k, v]) => `<strong>${this.esc(k)}</strong>: ${this.esc(v)}`
		);
		this.$("#pt-offer-counts").html(
			`${this.esc(o.name || "")}${parts.length ? " · " + parts.join(" · ") : ""}`
		);
	}

	copy_code() {
		const code = this.offer && this.offer.code;
		if (!code) return;
		if (navigator.clipboard && navigator.clipboard.writeText) {
			navigator.clipboard.writeText(code).then(
				() => frappe.show_alert({ message: __("Code copied."), indicator: "green" }),
				() => this.copy_fallback(code)
			);
		} else {
			this.copy_fallback(code);
		}
	}

	copy_fallback(code) {
		const ta = document.createElement("textarea");
		ta.value = code;
		document.body.appendChild(ta);
		ta.select();
		try {
			document.execCommand("copy");
			frappe.show_alert({ message: __("Code copied."), indicator: "green" });
		} catch (e) {
			this.fail(__("Copy failed"), __("Select the code manually."));
		}
		document.body.removeChild(ta);
	}

	// ---------------------------------------------------------------- RECEIVE

	connect() {
		if (this.busy) return;
		const code = this.$("#pt-code").val().trim();
		if (!code) {
			frappe.show_alert({ message: __("Paste an offer code first."), indicator: "orange" });
			return;
		}
		this.busy = true;
		this.$("#pt-conn-status").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Connecting...")}`);
		frappe.call({
			method: "peer_transfer.api.transfer.connect_with_code",
			args: { code: code },
			callback: (r) => {
				this.busy = false;
				this.$("#pt-conn-status").html("");
				if (!r.message) {
					this.fail(__("Connect failed"), __("Empty response from connect_with_code."));
					return;
				}
				this.conn = r.message;
				this.render_schema();
				if (this.schema_allows_accept(this.conn && this.conn.schema_diff)) {
					this.pv = { key: code, offset: 0, limit: 50, total: 0, rows: [] };
					this.load_preview();
				} else {
					// Blocked: hide preview, show decision card with Accept disabled.
					this.$("#pt-preview-card").hide();
					this.$("#pt-preview-body").html("");
					this.$("#pt-decision-card").show();
					this.$("#pt-receipt").html("");
					this.$("#pt-decision-status").html("");
					this.update_accept_state();
				}
			},
			error: (err) => {
				this.busy = false;
				this.$("#pt-conn-status").html("");
				this.fail(__("Connect failed"), this.esc(err.message || err));
			},
		});
	}

	// Normalize schema_diff across server versions.
	// New: {match, blocking:[], warnings:[], diffs:[], app_gaps:[]}.
	// Legacy: only {match, diffs} — treat legacy diffs as blocking.
	normalized_schema_diff(diff) {
		const d = diff || {};
		const has_new_keys =
			Object.prototype.hasOwnProperty.call(d, "blocking") ||
			Object.prototype.hasOwnProperty.call(d, "warnings") ||
			Object.prototype.hasOwnProperty.call(d, "app_gaps");
		const app_gaps = Array.isArray(d.app_gaps) ? d.app_gaps : [];
		let blocking = Array.isArray(d.blocking) ? d.blocking : [];
		let warnings = Array.isArray(d.warnings) ? d.warnings : [];
		const diffs = Array.isArray(d.diffs) ? d.diffs : [];
		if (!has_new_keys && diffs.length && !d.match) {
			blocking = diffs;
		}
		return {
			match: !!d.match,
			blocking: blocking,
			warnings: warnings,
			app_gaps: app_gaps,
			diffs: diffs,
		};
	}

	// Single gating authority: Accept is allowed only with zero
	// blocking entries and zero app gaps. Warnings never block.
	schema_allows_accept(diff) {
		const n = this.normalized_schema_diff(diff);
		return n.blocking.length === 0 && n.app_gaps.length === 0;
	}

	update_accept_state() {
		const allowed = this.schema_allows_accept(this.conn && this.conn.schema_diff);
		this.$("#pt-accept").prop("disabled", !allowed);
		return allowed;
	}

	schema_issue_table(rows) {
		if (!rows.length) return "";
		if (typeof rows[0] === "string") {
			return `<table class="table table-sm pt-table"><thead><tr><th>${__("Difference")}</th></tr></thead><tbody>` +
				rows.map((d) => `<tr class="pt-diff-row"><td>${this.esc(d)}</td></tr>`).join("") +
				`</tbody></table>`;
		}
		const cols = ["doctype", "field", "issue", "detail"];
		return `<table class="table table-sm pt-table"><thead><tr>` +
			cols.map((k) => `<th>${this.esc(k)}</th>`).join("") + `</tr></thead><tbody>` +
			rows.map((d) => `<tr class="pt-diff-row">` +
				cols.map((k) => `<td>${this.esc(d && d[k] != null ? d[k] : "")}</td>`).join("") +
				`</tr>`).join("") +
			`</tbody></table>`;
	}

	render_schema() {
		const c = this.conn;
		const n = this.normalized_schema_diff(c && c.schema_diff);
		const gaps = n.app_gaps;
		const blocking = n.blocking;
		const warnings = n.warnings;
		let html = `<div class="pt-card"><div class="pt-card-head"><i class="fa fa-check-square-o"></i>
			${__("Schema")} ${this.esc((c && c.name) || "")}</div><div class="pt-card-body">`;
		if (gaps.length) {
			const items = gaps.map((g) => {
				if (typeof g === "string") return `<li>${this.esc(g)}</li>`;
				const app = (g && (g.app || g.app_name)) || "";
				const ver = (g && (g.sender_version || g.version)) || "";
				const detail = (g && g.detail) || "";
				let line = `${__("Receiver is missing apps")}: ${this.esc(app)}` +
					(ver ? ` (${__("sender")} v${this.esc(ver)})` : "") +
					` — ${__("install on this site (bench get-app … + install-app + migrate), then reconnect.")}`;
				if (detail) line += ` <span class="text-muted">${this.esc(detail)}</span>`;
				return `<li>${line}</li>`;
			}).join("");
			html += `<div class="pt-banner pt-banner-red" id="pt-app-gaps"><span class="pt-banner-title">${__("Missing apps — cannot import")}</span><ul style="margin: 0; padding-left: 18px;">${items}</ul></div>`;
		} else if (blocking.length) {
			html += `<div class="pt-banner pt-banner-red" id="pt-blocked-banner"><span class="pt-banner-title">${__("BLOCKED — schema differences that would lose data")}</span>` +
				this.schema_issue_table(blocking) + `</div>`;
		} else if (warnings.length) {
			html += `<div class="pt-banner pt-banner-amber" id="pt-warnings-banner"><span class="pt-banner-title">${__("Non-blocking differences")} (${warnings.length}) — ${__("safe to proceed, these fields are UI-only or compatible")}</span>` +
				`<details class="pt-warn-details"><summary>${__("Show differences")} (${warnings.length})</summary>` +
				this.schema_issue_table(warnings) + `</details></div>`;
		} else if (n.match) {
			html += `<span class="pt-pill pt-pill-green" id="pt-match-pill">${__("SCHEMA MATCH")}</span>`;
		} else {
			// Fallback: no match flag and no categorized rows (e.g. empty legacy diff).
			html += `<span class="pt-pill pt-pill-red">${__("SCHEMA DIFF")}</span>`;
			html += `<div class="text-muted" style="margin-top:6px;">${__("Schemas differ (no detail returned).")}</div>`;
		}
		if (c && c.manifest) {
			html += `<div class="text-muted" style="margin-top: 8px; word-break: break-all;">
				<strong>${__("Manifest")}:</strong> ${this.esc(
					typeof c.manifest === "string" ? c.manifest : JSON.stringify(c.manifest)
				)}</div>`;
		}
		html += `</div></div>`;
		this.$("#pt-schema-panel").html(html);
		if (!this.schema_allows_accept(c && c.schema_diff)) {
			// Keep a stale preview from a previous connect from showing.
			this.$("#pt-preview-card").hide();
		} else {
			// Allowed (match or warnings-only): preview will be (re)filled
			// by load_preview; unhide in case a previous blocked connect hid it.
			this.$("#pt-preview-card").show();
		}
		this.update_accept_state();
	}

	load_preview() {
		this.$("#pt-preview-card").show();
		this.$("#pt-preview-body").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Loading preview...")}`);
		frappe.call({
			method: "peer_transfer.api.transfer.get_preview_rows",
			args: { code_or_name: this.pv.key, offset: this.pv.offset, limit: this.pv.limit },
			callback: (r) => {
				if (!r.message) {
					this.fail(__("Preview failed"), __("Empty response from get_preview_rows."));
					return;
				}
				this.pv.rows = r.message.rows || [];
				this.pv.total = r.message.total || 0;
				this.render_preview();
			},
			error: (err) => this.fail(__("Preview failed"), this.esc(err.message || err)),
		});
	}

	group_key(row) {
		if (row.doctype === "Customer") return row.name;
		return (
			row.data?.customer ||
			row.data?.links?.find((l) => l.link_doctype === "Customer")?.link_name ||
			__("Unknown")
		);
	}

	row_kind(row) {
		const dt = String(row.doctype || row.kind || row.type || "").toLowerCase();
		if (dt.includes("address")) return "address";
		if (dt.includes("contact")) return "contact";
		return "root";
	}

	is_primary(row, kind) {
		if (kind === "address") {
			return row.is_primary_address || row.is_primary || row.primary;
		}
		return (
			row.is_primary_contact ||
			row.is_primary_mobile_no ||
			row.is_primary_phone ||
			row.is_primary ||
			row.primary
		);
	}

	row_label(row, kind) {
		if (kind === "address") {
			return (
				row.address_title ||
				row.name ||
				[row.address_line1, row.city].filter(Boolean).join(", ")
			);
		}
		if (kind === "contact") {
			return row.full_name || row.first_name || row.name || row.email_id || "";
		}
		return row.customer_name || row.title || row.name || "";
	}

	row_sub(row, kind) {
		if (kind === "address") {
			return [row.city, row.mobile_no, row.phone, row.email_id].filter(Boolean).join(" · ");
		}
		if (kind === "contact") {
			return [row.mobile_no, row.phone, row.email_id].filter(Boolean).join(" · ");
		}
		const bits = [];
		if (row.name) bits.push(row.name);
		if (row.customer_name && row.customer_name !== row.name) bits.push(row.customer_name);
		return bits.join(" · ");
	}

	render_preview() {
		const groups = {};
		const order = [];
		(this.pv.rows || []).forEach((row) => {
			const key = this.group_key(row);
			if (!groups[key]) {
				groups[key] = { key: key, roots: [], addresses: [], contacts: [] };
				order.push(key);
			}
			const kind = this.row_kind(row);
			if (kind === "address") groups[key].addresses.push(row);
			else if (kind === "contact") groups[key].contacts.push(row);
			else groups[key].roots.push(row);
		});

		let html = "";
		if (!order.length) {
			html = `<span class="text-muted">${__("No preview rows.")}</span>`;
		}
		order.forEach((key) => {
			const g = groups[key];
			html += `<div class="pt-cust-card">
				<div class="pt-cust-head"><i class="fa fa-user"></i> ${this.esc(key)}
					<span class="text-muted" style="font-weight:400;">
						${g.roots.length} ${__("record(s)")} · ${g.addresses.length} ${__("address(es)")} · ${g.contacts.length} ${__("contact(s)")}
					</span>
				</div>`;
			if (g.roots.length) {
				html += `<div class="pt-cust-sec"><div class="pt-cust-sec-title">${__("Customer")}</div>` +
					g.roots.map((row) => `<div><strong>${this.esc(this.row_label(row, "root"))}</strong>
						<span class="text-muted"> ${this.esc(this.row_sub(row, "root"))}</span></div>`).join("") +
					`</div>`;
			}
			if (g.addresses.length) {
				html += `<div class="pt-cust-sec"><div class="pt-cust-sec-title">${__("Addresses")}</div>` +
					g.addresses.map((row) => `<div><i class="fa fa-map-marker"></i>
						${this.esc(this.row_label(row, "address"))}
						<span class="text-muted"> ${this.esc(this.row_sub(row, "address"))}</span>
						${this.is_primary(row, "address") ? `<span class="pt-badge">${__("Primary")}</span>` : ""}
					</div>`).join("") + `</div>`;
			}
			if (g.contacts.length) {
				html += `<div class="pt-cust-sec"><div class="pt-cust-sec-title">${__("Contacts")}</div>` +
					g.contacts.map((row) => `<div><i class="fa fa-phone"></i>
						${this.esc(this.row_label(row, "contact"))}
						<span class="text-muted"> ${this.esc(this.row_sub(row, "contact"))}</span>
						${this.is_primary(row, "contact") ? `<span class="pt-badge">${__("Primary")}</span>` : ""}
					</div>`).join("") + `</div>`;
			}
			html += `</div>`;
		});

		const pages = Math.max(1, Math.ceil(this.pv.total / this.pv.limit));
		const page = Math.floor(this.pv.offset / this.pv.limit) + 1;
		html += `<div class="pt-pager">
			<button class="btn btn-xs btn-secondary" id="pt-prev" ${this.pv.offset <= 0 ? "disabled" : ""}>
				<i class="fa fa-chevron-left"></i> ${__("Prev")}
			</button>
			<span class="text-muted">${__("Page")} ${page} ${__("of")} ${pages} (${this.pv.total} ${__("rows")})</span>
			<button class="btn btn-xs btn-secondary" id="pt-next"
				${this.pv.offset + this.pv.limit >= this.pv.total ? "disabled" : ""}>
				${__("Next")} <i class="fa fa-chevron-right"></i>
			</button>
		</div>`;

		this.$("#pt-preview-body").html(html);
		this.update_accept_state();
		this.$("#pt-prev").on("click", () => {
			this.pv.offset = Math.max(0, this.pv.offset - this.pv.limit);
			this.load_preview();
		});
		this.$("#pt-next").on("click", () => {
			if (this.pv.offset + this.pv.limit < this.pv.total) {
				this.pv.offset += this.pv.limit;
				this.load_preview();
			}
		});
		this.$("#pt-decision-card").show();
		this.$("#pt-receipt").html("");
		this.$("#pt-decision-status").html("");
	}

	accept() {
		if (this.busy || !this.pv.key) return;
		if (!this.schema_allows_accept(this.conn && this.conn.schema_diff)) {
			frappe.show_alert({ message: __("Schema check blocks import. Resolve the differences first."), indicator: "red" });
			return;
		}
		frappe.confirm(
			__("This will import the transfer into the current site. Continue?"),
			() => {
				this.busy = true;
				this.$("#pt-decision-status").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Importing...")}`);
				frappe.call({
					method: "peer_transfer.api.transfer.accept_and_import",
					args: { code_or_name: this.pv.key },
					callback: (r) => {
						this.busy = false;
						this.$("#pt-decision-status").html("");
						const rc = r.message || {};
						this.$("#pt-receipt").html(`
							<span class="pt-pill pt-pill-green">${this.esc(rc.created ?? 0)} ${__("created")}</span>
							<span class="pt-pill pt-pill-gray">${this.esc(rc.skipped ?? 0)} ${__("skipped")}</span>
							<span class="pt-pill ${rc.failed && rc.failed.length ? "pt-pill-red" : "pt-pill-gray"}">${this.esc((rc.failed || []).length)} ${__("failed")}</span>
						`);
						this.refresh_status();
						this.load_log();
					},
					error: (err) => {
						this.busy = false;
						this.$("#pt-decision-status").html("");
						this.fail(__("Import failed"), this.esc(err.message || err));
					},
				});
			}
		);
	}

	reject() {
		if (this.busy || !this.pv.key) return;
		frappe.confirm(
			__("Reject this transfer? The offer will be discarded."),
			() => {
				this.busy = true;
				this.$("#pt-decision-status").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Rejecting...")}`);
				frappe.call({
					method: "peer_transfer.api.transfer.reject_transfer",
					args: { code_or_name: this.pv.key },
					callback: (r) => {
						this.busy = false;
						this.$("#pt-decision-status").html("");
						if (r.message && r.message.ok) {
							frappe.show_alert({ message: __("Transfer rejected."), indicator: "orange" });
						}
						this.refresh_status();
						this.load_log();
					},
					error: (err) => {
						this.busy = false;
						this.$("#pt-decision-status").html("");
						this.fail(__("Reject failed"), this.esc(err.message || err));
					},
				});
			}
		);
	}

	refresh_status() {
		if (!this.pv.key) return;
		frappe.call({
			method: "peer_transfer.api.transfer.transfer_status",
			args: { code_or_name: this.pv.key },
			callback: (r) => {
				const s = r.message || {};
				const pill =
					s.status === "Imported" || s.status === "Accepted"
						? "pt-pill-green"
						: s.status === "Rejected"
							? "pt-pill-red"
							: "pt-pill-blue";
				this.$("#pt-decision-status").html(
					`<span class="pt-pill ${pill}">${this.esc(s.status || "")}</span>`
				);
			},
			error: () => {},
		});
	}

	// --------------------------------------------------------------------- LOG

	status_pill(status) {
		const s = String(status || "");
		const cls = /import|accept|complete|success/i.test(s)
			? "pt-pill-green"
			: /reject|fail|expire/i.test(s)
				? "pt-pill-red"
				: /pending|offer|open|draft/i.test(s)
					? "pt-pill-blue"
					: "pt-pill-gray";
		return `<span class="pt-pill ${cls}">${this.esc(s || "-")}</span>`;
	}

	load_log() {
		this.$("#pt-log-body").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}`);
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Peer Transfer",
				fields: ["name", "status", "creation", "modified"],
				order_by: "modified desc",
				limit_page_length: 50,
			},
			callback: (r) => this.render_log(r.message || []),
			error: (err) => this.fail(__("Log load failed"), this.esc(err.message || err)),
		});
	}

	render_log(rows) {
		if (!rows.length) {
			this.$("#pt-log-body").html(`<span class="text-muted">${__("No transfers yet.")}</span>`);
			return;
		}
		this.$("#pt-log-body").html(`
			<table class="table table-sm pt-table"><thead><tr>
				<th>${__("ID")}</th><th>${__("Status")}</th>
				<th>${__("Created")}</th><th>${__("Modified")}</th>
			</tr></thead><tbody>
			${rows.map((d) => `<tr class="pt-log-row" data-name="${this.esc(d.name)}">
				<td><strong>${this.esc(d.name)}</strong></td>
				<td>${this.status_pill(d.status)}</td>
				<td class="text-muted">${this.esc(d.creation || "")}</td>
				<td class="text-muted">${this.esc(d.modified || "")}</td>
			</tr>`).join("")}
			</tbody></table>
		`);
		this.$("#pt-log-body .pt-log-row").on("click", (e) => {
			const name = $(e.currentTarget).data("name");
			frappe.set_route("Form", "Peer Transfer", name);
		});
	}
};
