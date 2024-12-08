class MenuItem {
    constructor(name, parent, container) {
        this.name = name;
        this.parent = parent || null;
        this.submenuItems = [];
        this.isHighlighted = false;

        this.element = document.createElement('div');
        this.element.className = parent ? 'nested-menu-item' : 'top-level-menu-item';

        this.headerContainer = document.createElement('div');
        this.headerContainer.classList.add('menu-header');
        this.element.appendChild(this.headerContainer);

        this.toggleButton = document.createElement('span');
        this.toggleButton.style.fontFamily = 'monospace';
        this.toggleButton.style.cursor = 'pointer';
        this.toggleButton.onclick = () => this.toggleSubmenu();

        this.text = document.createElement('span');
        this.text.classList.add('menu-item-text');
        this.text.textContent = name;
        this.text.style.cursor = 'pointer';

        this.highlightIndicator = document.createElement('span');
        this.highlightIndicator.textContent = '●';
        this.highlightIndicator.style.color = 'red';
        this.highlightIndicator.style.display = 'none';

        this.submenuCount = document.createElement('span');
        this.submenuCount.classList.add('menu-item-count');
        this._disableCountUpdateCallback = null;

        this.headerContainer.appendChild(this.toggleButton);
        this.headerContainer.appendChild(this.highlightIndicator);
        this.headerContainer.appendChild(this.text);
        this.headerContainer.appendChild(this.submenuCount);

        this.submenuContainer = document.createElement('div');
        this.submenuContainer.classList.add('menu-container');
        this.submenuContainer.style.display = 'none';
        this.element.appendChild(this.submenuContainer);

        if (this.parent) {
            this.parent.submenuItems.push(this);
            this.parent.submenuContainer.appendChild(this.element);
            this.parent.toggleSubmenu(this.parent.parent === null);
        } else if (container) {
            container.appendChild(this.element);
        }
    }

    submenuIsOpen() {
        return this.submenuContainer.style.display !== 'none';
    }

    toggleSubmenu(open) {
        let isVisible = this.submenuIsOpen();
        if (typeof open === 'boolean') {
            isVisible = !open;
        }
        this.submenuContainer.style.display = isVisible ? 'none' : 'block';
        this.toggleButton.textContent = isVisible ? '[+]' : '[-]';
    }

    highlight() {
        this.isHighlighted = true;
        this.highlightIndicator.style.display = 'inline';
        if (this.parent) {
            this.parent.highlight();
        }
    }

    clearHighlight() {
        this.isHighlighted = false;
        this.highlightIndicator.style.display = 'none';
        this.submenuItems.forEach((item) => item.clearHighlight());
    }

    updateSubmenuCount() {
        const count = this.submenuContainer.childNodes.length;
        if (!count) {
            this.submenuCount.innerText = '';
            return;
        }
        this.submenuCount.innerText = `[${count}]`;
    }

    showSubmenuCount(show = true) {
        if (show) {
            if (this._disableCountUpdateCallback instanceof Function) {
                return;
            }
            this._disableCountUpdateCallback = observeChildMutations(this.submenuContainer, () => {
                this.updateSubmenuCount();
            });
        } else {
            if (this._disableCountUpdateCallback instanceof Function) {
                this._disableCountUpdateCallback();
                this._disableCountUpdateCallback = null;
                this.headerContainer.innerText = '';
            }
        }
    }

    addSubmenu(name) {
        const newItem = new MenuItem({ name, parent: this });
        this.submenuContainer.appendChild(newItem.element);
        return newItem;
    }

    rename(newName) {
        this.text.textContent = newName;
    }

    remove() {
        if (this.parent) {
            this.parent.submenuItems = this.parent.submenuItems.filter((item) => item !== this);
        }
        this.element.remove();
    }

    getElement() {
        return this.element;
    }

    registerScrollHandler(targetElement) {
        this.text.onclick = () => {
            scrollIntoView(targetElement);
        };
    }

    scrollTo() {
        if (this.text.onclick instanceof Function) {
            this.text.click();
        }
    }
}
