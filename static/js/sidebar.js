(function () {
    'use strict';

    var STORAGE_KEY = 'sidebarCollapsed';
    var ROOT = document.documentElement;
    var toggleButton = null;

    function isCollapsed() {
        return localStorage.getItem(STORAGE_KEY) === 'true';
    }

    function setCollapsed(collapsed) {
        ROOT.classList.toggle('sidebar-collapsed', collapsed);
        localStorage.setItem(STORAGE_KEY, collapsed ? 'true' : 'false');
        updateToggleButton(collapsed);
    }

    function updateToggleButton(collapsed) {
        if (!toggleButton) {
            return;
        }
        toggleButton.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        toggleButton.setAttribute(
            'aria-label',
            collapsed ? 'Expandir menu lateral' : 'Recolher menu lateral',
        );
    }

    function applySavedState() {
        setCollapsed(isCollapsed());
    }

    function initToggle() {
        toggleButton = document.getElementById('sidebarToggle');
        if (!toggleButton) {
            return;
        }
        toggleButton.addEventListener('click', function () {
            setCollapsed(!ROOT.classList.contains('sidebar-collapsed'));
        });
        updateToggleButton(ROOT.classList.contains('sidebar-collapsed'));
    }

    applySavedState();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initToggle);
    } else {
        initToggle();
    }
})();
