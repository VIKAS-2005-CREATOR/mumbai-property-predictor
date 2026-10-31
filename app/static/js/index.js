/* Valuation page: map + heatmap, area slider, local history, and PDF export.
 * Server values arrive through <script type="application/json"> blocks
 * (#page-config, #d-price, #d-insights) so this file has no template syntax.
 */
(function () {
    'use strict';

    const readJson = (id) => {
        const el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    };

    const config = readJson('page-config') || {};
    const basePrice = readJson('d-price');          // Crores, only after a valuation
    const insights = readJson('d-insights');

    const isHex = (value) => /^#[0-9a-fA-F]{6}$/.test(value);
    const inr = (value) => Math.round(value).toLocaleString('en-IN');

    // 1. Map (OpenStreetMap + Leaflet)
    function initMap() {
        const mapEl = document.getElementById('mumbaiMap');
        if (!mapEl || typeof L === 'undefined') return;

        const defaultCenter = [19.0760, 72.8777];
        const map = L.map('mumbaiMap', { center: defaultCenter, zoom: 11, scrollWheelZoom: false });

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
        }).addTo(map);

        // Enable wheel zoom only after clicking on the map
        map.once('focus', () => map.scrollWheelZoom.enable());

        const regionSelect = document.getElementById('region');
        const badge = document.getElementById('activeLocLabel');
        const coordsBadge = document.getElementById('mapCoordsBadge');
        let coords = {};
        let marker = null;
        let circle = null;

        function el(tag, props, children) {
            const node = document.createElement(tag);
            Object.assign(node, props || {});
            (children || []).forEach((child) => node.appendChild(child));
            return node;
        }

        function updateMap(regionName, fly) {
            if (badge) badge.textContent = regionName || '';
            const point = coords[regionName];
            if (!point) {
                if (coordsBadge) coordsBadge.textContent = 'Location not mapped';
                return;
            }
            if (!marker) {
                marker = L.marker(point).addTo(map);
                circle = L.circle(point, { radius: 1200, color: '#2563eb', fillColor: '#3b82f6', fillOpacity: 0.12, weight: 2 }).addTo(map);
            } else {
                marker.setLatLng(point);
                circle.setLatLng(point);
            }
            const popup = el('div', { className: 'map-popup' }, [
                el('strong', { textContent: '📍 ' + regionName }),
                el('div', { className: 'map-popup-sub', textContent: 'Selected for valuation' })
            ]);
            marker.bindPopup(popup);
            if (fly) map.flyTo(point, 14, { duration: 1.1 }); else map.setView(point, 14);
            if (coordsBadge) coordsBadge.textContent = point[0].toFixed(4) + '° N, ' + point[1].toFixed(4) + '° E';
        }

        function selectRegion(name) {
            if (!regionSelect) return;
            regionSelect.value = name;
            updateMap(name, true);
        }

        // Load coordinates for all regions from JSON file
        fetch(config.coordinatesUrl)
            .then((r) => r.json())
            .then((data) => {
                coords = data;
                if (regionSelect && regionSelect.value) updateMap(regionSelect.value, false);
            })
            .catch((err) => console.warn('Could not load region coordinates:', err));

        if (regionSelect) regionSelect.addEventListener('change', (e) => updateMap(e.target.value, true));

        // Clicking on map selects the nearest Mumbai locality
        map.on('click', (e) => {
            let nearest = null;
            let best = Infinity;
            Object.entries(coords).forEach(([name, point]) => {
                const d = Math.hypot(point[0] - e.latlng.lat, point[1] - e.latlng.lng);
                if (d < best) { best = d; nearest = name; }
            });
            if (nearest) selectRegion(nearest);
        });

        // Quick Zone Buttons
        const zones = {
            all:     { center: [19.0760, 72.8777], zoom: 11 },
            south:   { center: [18.9400, 72.8250], zoom: 13 },
            western: { center: [19.1200, 72.8350], zoom: 13 },
            central: { center: [19.0850, 72.9100], zoom: 13 },
            thane:   { center: [19.2183, 72.9781], zoom: 12 },
            navi:    { center: [19.0330, 73.0297], zoom: 12 }
        };
        document.querySelectorAll('[data-zone]').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('[data-zone]').forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                const target = zones[btn.dataset.zone] || zones.all;
                map.flyTo(target.center, target.zoom, { duration: 1.0 });
            });
        });

        // Price Heatmap Overlay
        const layer = L.layerGroup();
        const heatBtn = document.getElementById('heatToggleBtn');
        const legend = document.getElementById('heatmapLegend');
        let heatData = null;
        let heatOn = false;

        function renderLegend(tiers) {
            if (!legend) return;
            legend.querySelectorAll('.hl-item').forEach((n) => n.remove());
            tiers.forEach((tier) => {
                if (!isHex(tier.color)) return;
                const dot = el('span', { className: 'hl-dot' });
                dot.style.background = tier.color;
                legend.appendChild(el('span', { className: 'hl-item' }, [
                    dot, document.createTextNode(' ' + tier.label + ' (' + tier.range + ')')
                ]));
            });
        }

        function renderHeatmap(data) {
            layer.clearLayers();
            renderLegend(data.tiers || []);
            (data.regions || []).forEach((item) => {
                const color = isHex(item.color) ? item.color : '#64748b';
                const radius = Math.max(10, Math.min(28, 8 + item.avg_rate / 3500));
                const circleMarker = L.circleMarker([item.lat, item.lng], {
                    radius, color, weight: 1.5, fillColor: color, fillOpacity: item.low_data ? 0.25 : 0.55,
                    dashArray: item.low_data ? '3 3' : null
                });
                const title = el('strong', { textContent: '📍 ' + item.region });
                const tier = el('div', { className: 'map-popup-tier', textContent: item.label });
                tier.style.color = color;
                const stats = el('div', { className: 'map-popup-stats' }, [
                    el('div', { textContent: 'Median rate: ₹' + inr(item.avg_rate) + '/sq ft' }),
                    el('div', { textContent: 'Median price: ₹' + item.avg_price_cr + ' Cr' }),
                    el('small', { textContent: item.count + ' listings' + (item.low_data ? ' (limited data)' : '') })
                ]);
                const button = el('button', { type: 'button', className: 'map-popup-btn', textContent: 'Evaluate this area' });
                button.addEventListener('click', () => {
                    selectRegion(item.region);
                    map.closePopup();
                    const form = document.getElementById('predictor');
                    if (form) form.scrollIntoView({ behavior: 'smooth' });
                });
                circleMarker.bindPopup(el('div', { className: 'map-popup' }, [title, tier, stats, button]));
                layer.addLayer(circleMarker);
            });
            map.addLayer(layer);
        }

        if (heatBtn) {
            heatBtn.addEventListener('click', () => {
                heatOn = !heatOn;
                heatBtn.classList.toggle('active', heatOn);
                heatBtn.setAttribute('aria-pressed', String(heatOn));
                if (legend) legend.style.display = heatOn ? 'flex' : 'none';
                if (!heatOn) { map.removeLayer(layer); return; }
                if (heatData) { renderHeatmap(heatData); return; }
                fetch(config.heatmapUrl)
                    .then((r) => r.json())
                    .then((data) => { heatData = data; renderHeatmap(data); })
                    .catch((err) => console.error('Failed to load heatmap data:', err));
            });
        }

        const expandBtn = document.getElementById('expandMapBtn');
        const mapDiv = document.getElementById('mumbaiMap');
        if (expandBtn && mapDiv) {
            expandBtn.addEventListener('click', () => {
                mapDiv.classList.toggle('expanded');
                expandBtn.classList.toggle('active');

                const icon = expandBtn.querySelector('i');
                if (mapDiv.classList.contains('expanded')) {
                    icon.classList.replace('fa-expand', 'fa-compress');
                    expandBtn.title = 'Compress Map';
                } else {
                    icon.classList.replace('fa-compress', 'fa-expand');
                    expandBtn.title = 'Expand Map';
                }

                setTimeout(() => {
                    map.invalidateSize();
                }, 350);
            });
        }

        setTimeout(() => map.invalidateSize(), 350);
    }

    // 2. Area Slider <-> Number Input Sync
    function initAreaSync() {
        const slider = document.getElementById('area-slider');
        const number = document.getElementById('area');
        const label = document.getElementById('areaLabel');
        if (!slider || !number) return;

        const show = (value) => { if (label) label.textContent = value; };
        slider.addEventListener('input', () => { number.value = slider.value; show(slider.value); });
        number.addEventListener('input', () => { slider.value = number.value; show(number.value); });

        // Update area range dynamically based on BHK selection
        const bhkRadios = document.querySelectorAll('input[name="bhk"]');
        if (bhkRadios.length > 0) {
            const bhkDefaults = {
                1: { min: 200, max: 1200, value: 450 },
                2: { min: 400, max: 2000, value: 750 },
                3: { min: 700, max: 3500, value: 1100 },
                4: { min: 1200, max: 5000, value: 1800 },
                5: { min: 2000, max: 10000, value: 3000 }
            };

            bhkRadios.forEach(radio => {
                radio.addEventListener('change', (e) => {
                    const bhk = parseInt(e.target.value);
                    const config = bhkDefaults[bhk];
                    if (config) {
                        slider.min = config.min;
                        slider.max = config.max;

                        const minLabel = slider.parentElement.querySelector('.range-ends span');
                        if (minLabel) minLabel.textContent = config.min + ' sq ft';

                        if (e.isTrusted) {
                            slider.value = config.value;
                            number.value = config.value;
                            show(config.value);
                        }
                    }
                });
            });

            // Initialize on page load for the checked radio
            const checkedBhk = document.querySelector('input[name="bhk"]:checked');
            if (checkedBhk) {
                checkedBhk.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
    }

    // 3. Submit button state
    function initSubmitState() {
        const form = document.getElementById('valuationForm');
        const button = document.getElementById('submitBtn');
        if (!form || !button) return;
        const label = button.innerHTML;
        form.addEventListener('submit', () => {
            button.disabled = true;
            button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Calculating…';
        });
        window.addEventListener('pageshow', () => { button.disabled = false; button.innerHTML = label; });
    }

    // 4. PDF export
    function initPdfExport() {
        const button = document.getElementById('exportPdfBtn');
        const panel = document.getElementById('resultsPanel');
        if (!button || !panel) return;

        button.addEventListener('click', () => {
            const details = panel.querySelector('details');
            if (details) details.open = true;
            window.print();
        });
    }

    // 5. Local history (anonymous visitors only)
    function initLocalHistory() {
        const container = document.getElementById('localHistory');
        if (!container) return;
        const KEY = 'propval_hist';
        const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; } };
        const write = (items) => { try { localStorage.setItem(KEY, JSON.stringify(items)); } catch (e) { /* storage unavailable */ } };
        const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
        ));

        let hist = read();
        if (basePrice !== null) {
            const bhk = document.querySelector('[name=bhk]:checked');
            const record = {
                price: '₹' + parseFloat(basePrice).toFixed(2) + ' Cr',
                bhk: bhk ? bhk.value : '?',
                region: (document.getElementById('region') || {}).value || '?',
                area: (document.getElementById('area') || {}).value || '?',
                date: new Date().toLocaleDateString('en-IN')
            };
            const last = hist[0];
            if (!last || last.region !== record.region || last.bhk !== record.bhk || last.area !== record.area) {
                hist = [record].concat(hist).slice(0, 5);
                write(hist);
            }
        }
        if (hist.length) {
            container.innerHTML = hist.map((h) => `
                <div class="hist-item glass-panel">
                    <div class="hist-date">${esc(h.date)}</div>
                    <div class="hist-bhk">${esc(h.bhk)} BHK · ${esc(h.region)}</div>
                    <div class="hist-area"><i class="fa-solid fa-vector-square"></i> ${esc(h.area)} sq ft</div>
                    <div class="hist-price">${esc(h.price)}</div>
                </div>`).join('');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        initMap();
        initAreaSync();
        initSubmitState();
        initPdfExport();
        initLocalHistory();
    });
})();