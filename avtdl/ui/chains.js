class ChainCard {
    constructor(info, container) {
        this.info = info;

        this.parentContainer = container || createElement('div', 'card-container');
        this.container = createElement('div', 'chain-card');

        this.errorPlaceholder = addErrorPlaceholder(this.container);
        this.container.addEventListener('click', () => {
            clearInputError(this.container);
        });

        this.headerContainer = createElement('div', 'card-header-container', this.container);

        this.headerSelect = createElement('select', 'card-header', this.headerContainer);
        this.populateNestedDropdown(this.headerSelect, info.listTypes());
        this.headerSelect.value = null;
        this.headerSelect.onchange = () => {
            this.renderForHeader(this.headerSelect.value);
        };

        this.headerTooltip = null;

        this.itemsContainer = createElement('div', 'card-items', this.container);

        const addItemButton = createButton('[+]', () => this.addItem(), 'add-card-item-button');
        this.container.appendChild(addItemButton);

        this.parentContainer.appendChild(this.container);
    }

    populateFlatDropdown(selectElement, values) {
        selectElement.innerHTML = '';
        values.forEach((value) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            selectElement.appendChild(option);
        });
    }

    populateNestedDropdown(selectElement, values) {
        selectElement.innerHTML = '';
        for (const [group, options] of Object.entries(values)) {
            const optgroup = document.createElement('optgroup');
            optgroup.label = group;
            options.forEach((optionValue) => {
                const option = document.createElement('option');
                option.value = optionValue;
                option.textContent = optionValue;
                optgroup.appendChild(option);
            });
            selectElement.appendChild(optgroup);
        }
    }

    addItem(value = null) {
        const itemContainer = createElement('div', 'card-item-container');
        const header = this.headerSelect.value;

        let possibleValues = [];
        if (header) {
            possibleValues = this.info.listEntities(this.headerSelect.value);
        }

        const itemSelect = createElement('select', 'card-item-select', itemContainer);
        this.populateFlatDropdown(itemSelect, possibleValues);

        itemSelect.onchange = (event) => {
            this.updateItemHints(itemContainer, this.headerSelect.value, event.target.value);
        };

        const deleteButton = createButton(
            '[×]',
            () => {
                this.removeItem(itemContainer);
            },
            'inline-button'
        );
        deleteButton.title = 'remove line';
        itemContainer.appendChild(deleteButton);

        const definitionButton = createButton(
            '⤴',
            () => {
                this.info.scrollTo(this.headerSelect.value, itemSelect.value);
            },
            'inline-button'
        );
        definitionButton.title = 'go to entity definition';
        itemContainer.appendChild(definitionButton);

        if (value !== null) {
            itemSelect.value = value;
        } else {
            const usedValues = this.readItems();
            const freeValue = possibleValues.find((item) => !usedValues.includes(item)) || null;
            itemSelect.value = freeValue;
        }

        this.createItemHints(itemContainer);
        this.updateItemHints(itemContainer, this.headerSelect.value, itemSelect.value);

        this.itemsContainer.appendChild(itemContainer);
    }

    addItemHint(symbol, text, className, container) {
        const hint = createDefinition(symbol, text);
        hint.classList.add(className);
        hint.style.display = 'none';
        container.insertBefore(hint, container.firstChild);
    }

    createItemHints(itemContainer) {
        let text =
            'This entity has "consume_record" option enabled. ' +
            'It will not pass any incoming records down the chain. ' +
            'It may, however, produce records itself.';
        const hintConsumeRecord = this.addItemHint('⤓', text, 'hint-consume-record', itemContainer);

        text =
            'This entity has "reset_origin" option enabled. ' +
            'When used it multiple chains, it will pass incoming records ' +
            'from any of them to all of them.';
        const hintResetOrigin = this.addItemHint('⤋', text, 'hint-reset-origin', itemContainer);

        text =
            'This card lists multiple entities while being in the middle of the chain. ' +
            'Incoming records are fed into each of then in parallel, ' +
            'and the records each of them produce are passed down the chain.';
        const hintDuplicate = this.addItemHint('⚬', text, 'hint-duplicate', itemContainer);
    }

    showItemHint(className, hintContainer, show = true) {
        let hint = hintContainer.querySelector('.' + className);
        if (!hint) {
            return;
        }
        changeElementVisibility(hint, show);
    }

    cardPositionChanged(atEdgeOfChain = true) {
        const cardsContainers = Array.from(this.itemsContainer.children);
        cardsContainers.forEach((itemContainer) => {
            this.showItemHint('hint-duplicate', itemContainer, !atEdgeOfChain && cardsContainers.length > 1);
        });
    }

    updateItemHints(itemContainer, header, value) {
        const showConsumeRecord = this.info.getConsumeRecord(header, value) === true;
        this.showItemHint('hint-consume-record', itemContainer, showConsumeRecord);

        const showResetOrigin = this.info.getResetOrigin(header, value) === true;
        this.showItemHint('hint-reset-origin', itemContainer, showResetOrigin);
    }

    removeItem(itemContainer) {
        this.itemsContainer.removeChild(itemContainer);
    }

    renderForHeader(newHeaderValue) {
        this.itemsContainer.innerHTML = '';
        this.addItem();

        this.container.classList = ['chain-card'];
        const newType = this.info.actorType(newHeaderValue);
        if (newType) {
            this.container.classList.add(getActorTypeBgClass(newType));
        }

        if (this.headerTooltip) {
            this.headerContainer.removeChild(this.headerTooltip);
        }
        const description = this.info.listInfo(this.headerSelect.value);
        if (description) {
            this.headerTooltip = createTooltip(description);
            this.headerContainer.insertBefore(this.headerTooltip, this.headerSelect.nextSibling);
        }
    }

    readItems() {
        const items = [];
        const itemsSelectors = this.itemsContainer.querySelectorAll('select');
        for (const item of itemsSelectors) {
            if (item.value) {
                items.push(item.value);
            }
        }
        return items;
    }

    read() {
        const data = {};
        data[this.headerSelect.value] = this.readItems();
        return data;
    }

    fill(data) {
        this.headerSelect.value = data.header;
        this.renderForHeader(data.header);

        this.itemsContainer.innerHTML = '';
        data.items.forEach((item) => {
            this.addItem(item);
        });
    }

    isEmpty() {
        return !this.headerSelect.value || !this.readItems();
    }

    getElement() {
        return this.parentContainer;
    }

    showError(path, message) {
        if (path instanceof Array) {
            return showInputError(this.container, message);
        }
        return null;
    }
}

class ChainSection {
    constructor(name, data, info) {
        this.name = name;
        this.info = info;
        this.container = createFieldset(name);
        this.container.classList.add('chain-section');

        addErrorPlaceholder(this.container);
        this.container.addEventListener('click', () => {
            clearInputError(this.container);
        });

        this.cards = this.generateCards(data);
        observeChildMutations(this.container, () => this.handleReordering());
    }

    isEmpty() {
        return !this.name || !this.cards;
    }

    rename(newName) {
        this.name = newName;
        const legend = this.container.firstChild;
        if (!legend) {
            throw new Error(`error when renaming chain ${this.name}: container has no legend: ${this.container}`);
        }
        const legendText = legend.firstChild;
        if (!legendText) {
            throw new Error(`error when renaming chain ${this.name}: container has no legend text: ${this.container}`);
        }
        legendText.textContent = newName;
    }

    makeAddButton(referenceCard) {
        const addButton = createButton(
            '[+]',
            () => {
                this.addEmptyCard(referenceCard);
            },
            'card-button'
        );
        addButton.classList.add('add-card-button');
        addButton.title = 'insert new empty card after this one';
        return addButton;
    }

    generateCards(data) {
        let cards = [];
        if (data) {
            for (const cardData of data) {
                for (const [actorName, entities] of Object.entries(cardData)) {
                    const card = this.generateCard(actorName, entities);
                    cards.push(card);
                    this.container.appendChild(card.getElement());
                }
            }
        }
        return cards;
    }

    generateCard(actorName, entities, referenceNode = null) {
        const card = new ChainCard(this.info);
        if (actorName && entities) {
            card.fill({ header: actorName, items: entities });
        }

        const cardControls = createElement('div', 'card-controls');
        const deleteButton = createButton(
            '[×]',
            () => {
                this.deleteCard(card);
            },
            'card-button'
        );
        cardControls.appendChild(deleteButton);

        const backwardsButton = createButton(
            '[⇧]',
            () => {
                this.moveCard(card, true);
            },
            'card-button'
        );
        backwardsButton.title = 'move up';
        cardControls.appendChild(backwardsButton);

        const forwardButton = createButton(
            '[⇩]',
            () => {
                this.moveCard(card, false);
            },
            'card-button'
        );
        forwardButton.title = 'move down';
        cardControls.appendChild(forwardButton);

        const addButton = this.makeAddButton(card);
        cardControls.appendChild(addButton);

        const cardContainer = card.getElement();
        cardContainer.appendChild(cardControls);

        return card;
    }

    moveCard(card, backwards = false) {
        const index = this.cards.indexOf(card);
        if (index == -1 || (index == 0 && backwards) || (index == this.cards.length - 1 && !backwards)) {
            return;
        }

        this.container.removeChild(card.getElement());
        this.cards.splice(index, 1);

        const newIndex = backwards ? index - 1 : index + 1;
        const neighbour = this.cards[newIndex] && this.cards[newIndex].getElement();
        this.cards.splice(newIndex, 0, card);

        this.container.insertBefore(card.getElement(), neighbour);
    }

    addEmptyCard(referenceCard) {
        const card = this.generateCard();
        card.addItem();
        if (!referenceCard) {
            this.container.appendChild(card.getElement());
            this.cards.push(card);
        } else {
            this.container.insertBefore(card.getElement(), referenceCard.getElement().nextSibling);
            const position = this.cards.indexOf(referenceCard);
            this.cards.splice(position + 1, 0, card);
        }
    }

    deleteCard(card) {
        this.container.removeChild(card.getElement());
        this.cards = this.cards.filter((x) => x !== card);
        if (this.cards.length == 0) {
            this.addEmptyCard();
        }
    }

    handleReordering() {
        for (let i = 0; i < this.cards.length; i++) {
            const atEdgeOfChain = i == 0 || i == this.cards.length - 1;
            const card = this.cards[i];
            card.cardPositionChanged(atEdgeOfChain);
        }
    }

    getElement() {
        return this.container;
    }

    read() {
        const data = {};
        const cardsData = [];
        for (const card of this.cards) {
            if (!card.isEmpty()) {
                cardsData.push(card.read());
            }
        }
        data[this.name] = cardsData;
        return data;
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.container, message);
            } else if (path.length > 0) {
                const name = path[0];
                if (name in this.cards) {
                    return this.cards[name].showError(path.slice(1), message);
                }
            }
        }
        return null;
    }
}

class ChainsForm {
    constructor(data, menu, info) {
        this.container = document.createElement('div');
        this.container.classList.add('chains-form');
        this.menu = menu;
        this.info = info;
        this.chains = new OrderedDict();

        for (const [name, chainElements] of Object.entries(data)) {
            this.addChain(name, chainElements);
        }

        this.addButton = createButton(
            '[Add]',
            () => {
                this.addChain();
            },
            'add-chain-button'
        );
        this.addButton.classList.add('add-button');
        this.container.appendChild(this.addButton);
    }

    chooseChainName(base = 'Chain') {
        let name = chooseNewName(base, this.chains);
        if (name === null) {
            getUserInput('Name for a new chain:')
                .then((newName) => {
                    name = newName;
                })
                .catch(() => (name = ''));
        }
        return name;
    }

    addChain(name, chainElements) {
        name = name || this.chooseChainName();
        if (!name) {
            return;
        }
        const chainSection = new ChainSection(name, chainElements, this.info);
        this.chains[name] = chainSection;
        if (!chainElements) {
            chainSection.addEmptyCard();
        }
        const sectionContainer = this.wrapChain(chainSection);
        this.container.insertBefore(sectionContainer, this.addButton);
    }

    wrapChain(chainSection) {
        const menuItem = new MenuItem(chainSection.name, this.menu);
        menuItem.registerScrollHandler(chainSection.getElement());

        const chainContainer = document.createElement('div');
        chainContainer.className = 'chain';

        const legend = chainSection.getElement().firstChild;

        const renameButton = this.makeRenameButton(chainSection, menuItem);
        legend.appendChild(renameButton);

        const leftButton = this.makeMoveButton(chainSection, chainContainer, menuItem, '[⇦', false);
        legend.appendChild(leftButton);
        const rightButton = this.makeMoveButton(chainSection, chainContainer, menuItem, '⇨]', true);
        legend.appendChild(rightButton);

        const deleteButton = this.makeDeleteButton(chainSection, chainContainer, menuItem);
        legend.appendChild(deleteButton);

        chainContainer.appendChild(chainSection.getElement());

        return chainContainer;
    }

    makeRenameButton(chainSection, menuItem) {
        const checkName = (name) => {
            if (name in this.chains) {
                return `chain ${name} already exists`;
            }
            return null;
        };
        const renameButton = createButton(
            '[✎]',
            () => {
                getUserInput(`New name for "${chainSection.name}":`, chainSection.name, this.container, checkName).then(
                    (newName) => {
                        if (newName in this.chains) {
                            return;
                        }
                        delete this.chains[chainSection.name];
                        this.chains[newName] = chainSection;
                        chainSection.rename(newName);
                        menuItem.rename(newName);
                    }
                );
            },
            'inline-button'
        );
        renameButton.title = 'Rename chain';
        return renameButton;
    }

    makeDeleteButton(chainSection, chainContainer, menuItem) {
        const deleteChain = () => this.deleteChain(chainSection, chainContainer, menuItem);
        const deleteButton = createButton('[×]', () => deleteChain(), 'inline-button');
        deleteButton.classList.add('delete-chain-button');
        deleteButton.title = 'Delete chain';
        return deleteButton;
    }

    makeMoveButton(chainSection, chainContainer, menuItem, symbol, forward) {
        const moveChain = () => {
            this.moveChain(chainSection, chainContainer, menuItem, forward);
        };
        const moveButton = createButton(symbol, moveChain, 'inline-button');
        moveButton.title = forward ? 'Move forward' : 'Move back';
        return moveButton;
    }

    moveChain(chainSection, chainContainer, menuItem, forward = true) {
        if (chainContainer === this.container.firstChild && !forward) {
            return;
        }
        if (chainContainer === this.addButton.previousSibling && forward) {
            return;
        }
        const step = forward ? 1 : -1;
        this.chains.move(chainSection.name, step);
        moveElement(chainContainer, forward);
        moveElement(menuItem.getElement(), forward);
    }

    deleteChain(chainSection, chainContainer, menuItem) {
        this.container.removeChild(chainContainer);
        delete this.chains[chainSection.name];
        menuItem.remove();
    }

    duplicateChain(chainSection) {
        const data = chainSection.read();
    }

    getElement() {
        return this.container;
    }

    read() {
        let data = {};
        for (const [name, chain] of Object.entries(this.chains)) {
            if (!chain.isEmpty()) {
                data = { ...data, ...chain.read() };
            }
        }
        return data;
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length >= 2) {
                if (path[0] == 'chains') {
                    if (path[1] in this.chains) {
                        return this.chains[path[1]].showError(path.slice(2), message);
                    }
                }
            }
        }
    }
}
