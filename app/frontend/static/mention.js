/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * @Mention Popup Component
 * 
 * Listens to Deep Chat internal textarea input, detects '@' to trigger search popup.
 * On selection, inserts @[DisplayName](type:entityId) into the input.
 */
class MentionPopup {
    constructor() {
        this.popup = null;
        this.textarea = null;
        this.isVisible = false;
        this.selectedIndex = 0;
        this.results = [];
        this.searchQuery = '';
        this.atStartPos = -1;
        this.debounceTimer = null;

        this._waitForDeepChat();
    }

    /** Poll for Deep Chat shadow DOM textarea */
    _waitForDeepChat() {
        let attempts = 0;
        const poll = setInterval(() => {
            attempts++;
            const chat = document.getElementById('chat');
            if (!chat || !chat.shadowRoot) {
                if (attempts > 100) {
                    clearInterval(poll);
                    console.warn('[Mention] Deep Chat shadow root not found after 30s, giving up');
                }
                return;
            }

            // Deep Chat may use textarea or contenteditable div
            const ta = chat.shadowRoot.querySelector('textarea') ||
                       chat.shadowRoot.querySelector('input[type="text"]') ||
                       chat.shadowRoot.querySelector('[contenteditable="true"]');
            if (!ta) return;

            clearInterval(poll);
            this.textarea = ta;
            console.log('[Mention] Attached to input element:', ta.tagName);
            this._createPopup();
            this._attachListeners();
        }, 300);
    }

    _createPopup() {
        this.popup = document.getElementById('mention-popup');
        if (!this.popup) {
            this.popup = document.createElement('div');
            this.popup.id = 'mention-popup';
            this.popup.className = 'mention-popup hidden';
            this.popup.innerHTML = `
                <div class="mention-header">
                    <span class="mention-title">Select Entity</span>
                    <div class="mention-tabs">
                        <button class="mention-tab active" data-type="">All</button>
                        <button class="mention-tab" data-type="service">Service</button>
                        <button class="mention-tab" data-type="vm">VM</button>
                        <button class="mention-tab" data-type="ticket">Ticket</button>
                    </div>
                </div>
                <div class="mention-results"></div>
            `;
            document.body.appendChild(this.popup);
        }

        // Tab click handlers
        this.popup.querySelectorAll('.mention-tab').forEach(tab => {
            tab.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.popup.querySelectorAll('.mention-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this._doSearch(this.searchQuery, tab.dataset.type || null);
            });
        });
    }

    _attachListeners() {
        const ta = this.textarea;

        // Input event - works for both textarea and contenteditable
        ta.addEventListener('input', () => this._onInput());

        // Keyboard navigation (capture phase to intercept before Deep Chat)
        ta.addEventListener('keydown', (e) => this._onKeyDown(e), true);

        // Close popup on outside click
        document.addEventListener('mousedown', (e) => {
            if (this.isVisible && !this.popup.contains(e.target)) {
                this.hide();
            }
        });

        console.log('[Mention] Listeners attached');
    }

    _getInputValue() {
        if (this.textarea.tagName === 'TEXTAREA' || this.textarea.tagName === 'INPUT') {
            return this.textarea.value || '';
        }
        // contenteditable
        return this.textarea.textContent || '';
    }

    _getCursorPos() {
        if (this.textarea.tagName === 'TEXTAREA' || this.textarea.tagName === 'INPUT') {
            return this.textarea.selectionStart ?? this._getInputValue().length;
        }
        // contenteditable - use window selection
        const sel = this.textarea.getRootNode().getSelection
            ? this.textarea.getRootNode().getSelection()
            : window.getSelection();
        if (sel && sel.rangeCount > 0) {
            return sel.getRangeAt(0).startOffset;
        }
        return this._getInputValue().length;
    }

    _onInput() {
        const value = this._getInputValue();
        const cursorPos = this._getCursorPos();

        // Find the last '@' before cursor
        const textBeforeCursor = value.slice(0, cursorPos);
        const lastAtIdx = textBeforeCursor.lastIndexOf('@');

        if (lastAtIdx === -1) {
            this.hide();
            return;
        }

        // '@' must be at start or after whitespace
        if (lastAtIdx > 0 && !/\s/.test(value[lastAtIdx - 1])) {
            this.hide();
            return;
        }

        // Skip if this '@' is already part of a completed mention
        const afterAt = value.slice(lastAtIdx);
        if (/^@\[[^\]]+\]\([^)]+\)/.test(afterAt)) {
            this.hide();
            return;
        }

        // Extract search query after '@'
        const query = textBeforeCursor.slice(lastAtIdx + 1);
        if (query.length > 50) {
            this.hide();
            return;
        }

        this.atStartPos = lastAtIdx;
        this.searchQuery = query;
        this.show();

        // Debounced search
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => {
            const activeTab = this.popup.querySelector('.mention-tab.active');
            const type = activeTab ? activeTab.dataset.type || null : null;
            this._doSearch(query, type);
        }, 300);
    }

    _onKeyDown(e) {
        if (!this.isVisible) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            e.stopImmediatePropagation();
            this.selectedIndex = Math.min(this.selectedIndex + 1, this.results.length - 1);
            this._highlightItem();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            e.stopImmediatePropagation();
            this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
            this._highlightItem();
        } else if (e.key === 'Enter' && this.results.length > 0) {
            e.preventDefault();
            e.stopImmediatePropagation();
            this._selectItem(this.results[this.selectedIndex]);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            this.hide();
        }
    }

    async _doSearch(query, type) {
        const params = new URLSearchParams();
        if (query) params.set('q', query);
        if (type) params.set('type', type);
        params.set('limit', '10');

        try {
            const resp = await fetch(`/api/mention/search?${params.toString()}`);
            if (!resp.ok) {
                console.error('[Mention] Search API returned', resp.status);
                return;
            }
            const data = await resp.json();

            // Flatten results from all type groups
            this.results = [];
            for (const group of (data.results || [])) {
                for (const item of (group.items || [])) {
                    this.results.push({
                        type: group.type,
                        id: item.id,
                        name: item.name,
                        description: item.description || '',
                    });
                }
            }

            this.selectedIndex = 0;
            this._renderResults();
        } catch (err) {
            console.error('[Mention] Search failed:', err);
        }
    }

    _renderResults() {
        const container = this.popup.querySelector('.mention-results');
        if (this.results.length === 0) {
            container.innerHTML = '<div class="mention-empty">No results</div>';
            return;
        }

        container.innerHTML = this.results.map((item, idx) => `
            <div class="mention-item ${idx === this.selectedIndex ? 'active' : ''}" data-index="${idx}">
                <span class="mention-type-tag mention-type-${item.type}">${this._typeLabel(item.type)}</span>
                <div class="mention-item-info">
                    <span class="mention-item-name">${this._escapeHtml(item.name)}</span>
                    <span class="mention-item-desc">${this._escapeHtml(item.description)}</span>
                </div>
            </div>
        `).join('');

        // Use mousedown (not click) to prevent blur before selection
        container.querySelectorAll('.mention-item').forEach(el => {
            el.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const idx = parseInt(el.dataset.index);
                this._selectItem(this.results[idx]);
            });
        });
    }

    _highlightItem() {
        this.popup.querySelectorAll('.mention-item').forEach((el, idx) => {
            el.classList.toggle('active', idx === this.selectedIndex);
            if (idx === this.selectedIndex) {
                el.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    _selectItem(item) {
        if (!item) return;

        const mention = `@[${item.name}](${item.type}:${item.id}) `;
        const value = this._getInputValue();
        const cursorPos = this._getCursorPos();
        const before = value.slice(0, this.atStartPos);
        const after = value.slice(cursorPos);
        const newValue = before + mention + after;

        if (this.textarea.tagName === 'TEXTAREA' || this.textarea.tagName === 'INPUT') {
            // Use native setter to bypass framework wrappers
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            )?.set || Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            )?.set;

            if (nativeSetter) {
                nativeSetter.call(this.textarea, newValue);
            } else {
                this.textarea.value = newValue;
            }
            // Fire input event to notify Deep Chat
            this.textarea.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true }));

            // Position cursor after mention
            const newCursorPos = before.length + mention.length;
            this.textarea.setSelectionRange(newCursorPos, newCursorPos);
        } else {
            // contenteditable
            this.textarea.textContent = newValue;
            this.textarea.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true }));
        }

        this.textarea.focus();
        this.hide();
        console.log('[Mention] Inserted:', mention.trim());
    }

    show() {
        if (this.isVisible) return;
        this.isVisible = true;
        this.popup.classList.remove('hidden');
        this._positionPopup();
    }

    hide() {
        if (!this.isVisible) return;
        this.isVisible = false;
        this.popup.classList.add('hidden');
        this.searchQuery = '';
        this.atStartPos = -1;
        this.results = [];
        this.selectedIndex = 0;
    }

    _positionPopup() {
        const chat = document.getElementById('chat');
        if (!chat) return;
        const rect = chat.getBoundingClientRect();
        this.popup.style.bottom = (window.innerHeight - rect.bottom + 80) + 'px';
        this.popup.style.left = (rect.left + 24) + 'px';
        this.popup.style.width = Math.min(rect.width - 48, 480) + 'px';
    }

    _typeLabel(type) {
        const labels = { service: 'Service', vm: 'VM', ticket: 'Ticket' };
        return labels[type] || type;
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Auto-initialize
document.addEventListener('DOMContentLoaded', () => {
    window.mentionPopup = new MentionPopup();
});
