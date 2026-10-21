/* Small same-origin UI layer. External advert text is always inserted as text. */
'use strict';
const money = value => value == null ? 'Unavailable' : `${Math.round(value).toLocaleString('sv-SE')} SEK`;
const date = value => value ? new Date(value).toLocaleString('en-GB', {timeZone: 'Europe/Stockholm'}) : 'Not yet';
const toast = message => {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 7000);
};
async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  const body = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(e => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('; ') : body.detail;
    throw new Error(detail || 'Request failed');
  }
  return body;
}
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function favouriteButton(car) {
  const button = node('button', car.favourite ? '★' : '☆', 'favourite-button');
  button.type = 'button';
  button.dataset.favourite = car.id;
  button.setAttribute('aria-pressed', String(Boolean(car.favourite)));
  button.title = car.favourite ? 'Remove from favourites' : 'Add to favourites';
  button.setAttribute('aria-label', button.title);
  return button;
}

// Delegate clicks so both server-rendered and refreshed cards share the same action.
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-favourite]');
  if (!button || button.disabled) return;
  button.disabled = true;
  try {
    const result = await api(`/api/listings/${encodeURIComponent(button.dataset.favourite)}/favourite`, {
      method: 'PUT', body: JSON.stringify({favourite: button.getAttribute('aria-pressed') !== 'true'})
    });
    button.textContent = result.favourite ? '★' : '☆';
    button.setAttribute('aria-pressed', String(result.favourite));
    button.title = result.favourite ? 'Remove from favourites' : 'Add to favourites';
    button.setAttribute('aria-label', button.title);
    button.closest('.listing-card')?.classList.toggle('favourite-card', result.favourite);
    const favouritesList = button.closest('.favourites-list');
    if (favouritesList && !result.favourite) {
      button.closest('.listing-card').remove();
      if (!favouritesList.querySelector('.listing-card')) {
        favouritesList.append(node('div', 'No favourites yet. Use the star on a listing to save it here.', 'empty'));
      }
    }
    document.dispatchEvent(new Event('favourite-changed'));
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
});

document.querySelectorAll('[data-date]').forEach(el => {
  if (el.dataset.date) el.textContent = date(el.dataset.date);
});

document.querySelectorAll('[data-refresh]').forEach(button => {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const result = await api(`/api/searches/${button.dataset.refresh}/refresh`, {method: 'POST'});
      toast(result.queued ? 'Refresh queued. You can keep browsing.' : 'This search is already queued or refreshing.');
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  });
});

const form = document.querySelector('#search-form');
if (form) {
  const initial = JSON.parse(document.querySelector('#initial-config').textContent);
  Object.entries(initial.description_filters || {}).forEach(([key, value]) => {
    if (form.elements['fact_' + key]) form.elements['fact_' + key].value = value;
  });
  Object.entries(initial).forEach(([key, value]) => {
    if (form.elements[key] && value != null) form.elements[key].value = value;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]');
    const errorBox = document.querySelector('#form-error');
    errorBox.hidden = true;
    submit.disabled = true;
    const data = Object.fromEntries(new FormData(form));
    data.description_filters = {};
    Object.keys(data).filter(key => key.startsWith('fact_')).forEach(key => {
      if (data[key]) data.description_filters[key.slice(5)] = data[key];
      delete data[key];
    });
    ['price_from', 'price_to', 'year_from', 'year_to', 'milage_from', 'milage_to', 'deal_threshold'].forEach(key => {
      data[key] = data[key] === '' ? null : Number(data[key]);
    });
    try {
      const result = await api(`/api/searches${form.dataset.id ? '/' + form.dataset.id : ''}`, {
        method: form.dataset.id ? 'PUT' : 'POST', body: JSON.stringify(data)
      });
      location.href = `/searches/${result.id}`;
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
    finally { submit.disabled = false; }
  });
  document.querySelector('#delete-search')?.addEventListener('click', async () => {
    if (!confirm('Delete this saved search? Collected observations remain available for the model.')) return;
    try { await api(`/api/searches/${form.dataset.id}`, {method: 'DELETE'}); location.href = '/'; }
    catch (error) { toast(error.message); }
  });
}

const searchPage = document.querySelector('#search-page');
if (searchPage) {
  const id = searchPage.dataset.id;
  const favouritesFirst = document.querySelector('#favourites-first');
  const favouritesPreference = `favourites-first-${id}`;
  try { favouritesFirst.checked = localStorage.getItem(favouritesPreference) === 'true'; }
  catch (_) { /* Sorting remains usable when browser storage is unavailable. */ }
  favouritesFirst.addEventListener('change', () => {
    try { localStorage.setItem(favouritesPreference, String(favouritesFirst.checked)); }
    catch (_) { /* Keep this page's choice even if it cannot be persisted. */ }
    load();
  });
  document.addEventListener('favourite-changed', () => load());
  let map, layers, firstBounds = true, loading = false, reloadPending = false, signature = '';
  const markers = new Map();
  if (window.L) {
    map = L.map('map').setView([62.2, 15.6], 5);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    layers = L.featureGroup().addTo(map);
  } else {
    document.querySelector('#map').textContent = 'Map library unavailable. Listings remain accessible below.';
  }
  function focusCard(carId) {
    document.querySelectorAll('.listing-card.selected').forEach(el => el.classList.remove('selected'));
    const card = document.getElementById(`car-${carId}`);
    if (card) { card.classList.add('selected'); card.scrollIntoView({behavior: 'smooth', block: 'center'}); }
  }
  function card(car) {
    const article = node('article', null, `listing-card${car.highlight ? ' highlight' : car.above_estimate ? ' over-estimate' : ''}`);
    article.id = `car-${car.id}`;
    article.classList.toggle('favourite-card', Boolean(car.favourite));
    if (car.images.length) {
      const img = node('img', null, 'car-image');
      img.src = car.images[0]; img.alt = car.title; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
      article.append(img);
    } else { article.append(node('div', 'No photo', 'car-image no-image')); }
    const body = node('div', null, 'listing-body');
    const meta = node('div', null, 'listing-meta');
    meta.append(node('span', `${car.seller_type} seller`));
    const actions = node('div', null, 'listing-card-actions');
    actions.append(favouriteButton(car));
    body.append(actions, meta);
    const priceBadges = node('div', null, 'listing-price-badges');
    if (car.highlight) priceBadges.append(node('span', `${car.discount.toFixed(1)}% below estimate`, 'deal-badge'));
    if (car.above_estimate) priceBadges.append(node('span', `${Math.abs(car.discount).toFixed(1)}% above estimate`, 'deal-badge over-estimate-badge'));
    if (!car.active) priceBadges.append(node('span', 'Not seen in latest complete search', 'badge'));
    if (priceBadges.childNodes.length) actions.append(priceBadges);
    const facts = {...(car.analysis?.status === 'ready' ? car.analysis.facts : {})};
    const labels = {drivable: 'Drivable', extra_tires: 'Additional tyres', winter_tires: 'Winter tyres', summer_tires: 'Summer tyres'};
    const findings = node('div', null, 'finding-badges');
    Object.entries(facts || {}).forEach(([key, fact]) => {
      if (!labels[key] || fact.assumed) return;
      if (fact.value !== true && !(key === 'drivable' && fact.value === false)) return;
      const badge = node('span', key === 'drivable' && fact.value === false ? 'Not drivable' : labels[key], key === 'drivable' && fact.value === false ? 'badge finding-risk' : 'badge finding-yes');
      badge.title = `LLM finding: ${fact.evidence}`; findings.append(badge);
    });
    if (findings.childNodes.length) body.append(findings);
    const title = node('h3');
    const link = node('a', car.title); link.href = `/listings/${car.id}`; title.append(link); body.append(title);
    body.append(node('p', [car.year, car.mileage == null ? 'Mileage unknown' : `${car.mileage.toLocaleString('sv-SE')} km`, car.fuel].filter(Boolean).join(' · ')));
    if (car.variant) body.append(node('p', car.variant));
    body.append(node('div', money(car.price), 'listing-price'));
    body.append(node('div', car.estimate ? `Estimated ${money(car.estimate)} · ${car.comparable_count} comparables` : 'Estimated asking price: not enough data', 'estimate'));
    const bottom = node('p');
    bottom.append(node('span', `${car.location || 'Location unavailable'}${car.approximate ? ' (approximate)' : ''} `));
    if (map && car.lat != null && car.lon != null) {
      const button = node('button', 'Show on map ↗', 'pin-button');
      button.addEventListener('click', () => {
        map.setView([car.lat, car.lon], 11); markers.get(car.id)?.openPopup();
        if (innerWidth < 1000) document.querySelector('#map').scrollIntoView({behavior: 'smooth'});
      });
      bottom.append(button);
    }
    body.append(bottom); article.append(body);
    return article;
  }
  function renderModel(model, search) {
    document.querySelector('#model-name').textContent = model.name ? `Shared model: ${model.name}` : 'Not enough data';
    document.querySelector('#model-summary').textContent = `Shared training pool across all searches: ${model.sample_size || 0} usable adverts, ${model.description_analyzed_count || 0} with LLM description facts; ${model.excluded_count || 0} excluded for missing required fields. One observation per advert from the last ${model.training_window_days || 180} days. Updated ${date(model.trained_at)}.`;
    document.querySelector('#model-factors').textContent = `Factors: ${(model.features || []).map(f => f === 'year' ? 'model year' : f === 'mileage' ? 'mileage (km)' : f.replaceAll('_', ' ')).join(', ') || 'Waiting for training'}.`;
    document.querySelector('#model-year-method').textContent = model.year_method ? `How year is used: ${model.year_method} Across the training sample, increasing the year by one while keeping other fields fixed changes the fitted estimate by a median ${model.year_effect_percent}%. This is a model response, not a guaranteed appreciation rate.` : 'Year effects will appear after training.';
    const local = model.search_group;
    const ready = model.status === 'ready' && local && local.count >= (model.minimum_comparables || 20) && local.mape != null;
    const error = ready ? local.mape : null;
    document.querySelector('#model-quality').textContent = error == null ? `${local?.count || 0} usable comparables for this make/model. Estimates need at least 100 usable adverts overall and 20 for this make/model. Other searches’ accuracy does not describe this car.` : `This make/model: ${local.count} adverts, ${local.mape}% average absolute percentage error, ${money(local.mae_sek)} average absolute error. ${error >= search.config.deal_threshold ? 'The average prediction error is at least as large as your highlight threshold. Treat highlights as leads to investigate.' : 'A below-estimate price still needs review of the car and advert.'}`;
    const comparison = document.querySelector('#model-comparison'); comparison.replaceChildren();
    (model.candidates || []).forEach(candidate => {
      if (!ready || !candidate.search_metrics) return;
      const row = node('tr');
      [candidate.name + (candidate.id === model.algorithm ? ' (selected)' : ''), `${candidate.search_metrics.mape}%`, money(candidate.search_metrics.mae_sek)].forEach(value => row.append(node('td', value)));
      comparison.append(row);
    });
    const years = document.querySelector('#model-years'); years.replaceChildren();
    (local?.years || []).forEach(year => {
      const row = node('tr');
      [year.year, year.count, money(year.median_asking), money(year.median_estimated), `${year.mape}%`].forEach(value => row.append(node('td', value)));
      years.append(row);
    });
    if (!local?.years?.length) {
      const row = node('tr'); const cell = node('td', 'Year diagnostics will appear after training on this make/model.'); cell.colSpan = 5; row.append(cell); years.append(row);
    }
  }
  async function load() {
    if (loading) { reloadPending = true; return; }
    loading = true;
    try {
      const params = new URLSearchParams({seller: document.querySelector('#seller-filter').value,
        sort: document.querySelector('#sort-filter').value,
        deals: document.querySelector('#deals-filter').checked,
        archived: document.querySelector('#archived-filter').checked});
      document.querySelectorAll('.finding-grid input:checked').forEach(input => {
        if (input.value) params.set('fact_' + input.dataset.fact, input.value);
      });
      const [data, status] = await Promise.all([api(`/api/searches/${id}/listings?${params}`), api('/api/status')]);
      const {search, listings: cars, model} = data;
      if (favouritesFirst.checked) cars.sort((a, b) => Number(b.favourite) - Number(a.favourite));
      renderModel(model, search);
      const analysis = status.description_analysis || {};
      document.querySelector('#analysis-status').textContent = analysis.enabled ? `Local LLM: ${analysis.model} · ${analysis.ready || 0} analyzed · ${analysis.pending || 0} pending${analysis.errors ? ` · ${analysis.errors} retrying` : ''}` : 'Description analysis disabled — configure the external Ollama connection.';
      const running = status.running === Number(id), queued = status.queued.includes(Number(id));
      document.querySelector('#collection-timestamp').textContent = `${running ? 'Refreshing · ' : queued ? 'Queued · ' : ''}Collected: ${date(search.last_success)}`;
      document.querySelector('#collection-status').textContent = `${running ? 'Refreshing' : queued ? 'Queued' : search.status} · Last successful collection: ${date(search.last_success)}. ${search.message}${search.pending_config ? ' Changes saved. A refresh with your latest settings will run after the current refresh finishes.' : ''}`;
      document.querySelector('#listing-count').textContent = cars.length;
      document.querySelector('#deal-count').textContent = cars.filter(c => c.highlight || c.above_estimate).length;
      const local = model.search_group;
      document.querySelector('#sample-count').textContent = local?.count || 0;
      const localError = model.status === 'ready' && local && local.count >= (model.minimum_comparables || 20) ? local.mape : null;
      document.querySelector('#model-error').textContent = localError == null ? 'Not ready' : `${localError}%`;
      const missing = cars.filter(c => c.lat == null || c.lon == null).length;
      document.querySelector('#results-note').textContent = `${missing} without map coordinates. ${localError != null ? `Validation error for this make/model: ${money(local.mae_sek)}. Model updated ${date(model.trained_at)}.` : 'Estimates need 100 usable adverts overall and 20 comparables for this make/model.'} Missing year/mileage prevents an estimate. A highlight is a price signal; review condition and advert details.`;
      const nextSignature = JSON.stringify({cars, busy: running || queued});
      if (nextSignature !== signature) {
        signature = nextSignature;
        const results = document.querySelector('#results'); results.replaceChildren();
        if (!cars.length) results.append(node('div', running || queued ? 'Collecting your first matches. Results will appear automatically.' : 'No matching listings. Adjust filters, or check collection status above.', 'empty'));
        layers?.clearLayers(); markers.clear();
        cars.forEach(car => {
          results.append(card(car));
          if (map && car.lat != null && car.lon != null) {
            const popup = node('div');
            const link = node('a', car.title); link.href = `/listings/${car.id}`;
            popup.append(link, node('div', `${money(car.price)} · ${car.seller_type}`), node('div', `${car.location}${car.approximate ? ' (approximate)' : ''}`));
            const marker = L.circleMarker([car.lat, car.lon], {radius: car.highlight || car.above_estimate ? 9 : 6,
              fillColor: car.highlight ? '#b3dd80' : car.above_estimate ? '#c9554d' : '#17392f', color: '#fff', weight: 2, fillOpacity: .95})
              .bindPopup(popup).addTo(layers);
            marker.on('click', () => focusCard(car.id)); markers.set(car.id, marker);
          }
        });
        if (map && firstBounds && layers.getLayers().length) {
          map.fitBounds(layers.getBounds(), {padding: [25, 25], maxZoom: 11}); firstBounds = false;
        }
        document.querySelector('#map-count').textContent = `${cars.length - missing} on map`;
      }
    } catch (error) {
      document.querySelector('#collection-timestamp').textContent = 'Collection: could not load';
      document.querySelector('#collection-status').textContent = `Could not load results: ${error.message}`;
    }
    finally {
      loading = false;
      if (reloadPending) { reloadPending = false; load(); }
    }
  }
  ['seller-filter', 'sort-filter', 'deals-filter', 'archived-filter'].forEach(id => {
    document.getElementById(id).addEventListener('change', () => { firstBounds = true; load(); });
  });
  document.querySelectorAll('.finding-grid input').forEach(input => {
    input.addEventListener('change', () => {
      const count = [...document.querySelectorAll('.finding-grid input:checked')].filter(choice => choice.value).length;
      const badge = document.querySelector('#finding-filter-count');
      badge.textContent = `${count} active`;
      badge.hidden = count === 0;
      firstBounds = true;
      load();
    });
  });
  load(); setInterval(() => { if (!document.hidden) load(); }, 10000);
}

const descriptionPanel = document.querySelector('#description-analysis');
if (descriptionPanel && ['pending', 'error'].includes(descriptionPanel.dataset.status)) {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const item = await api(`/api/listings/${descriptionPanel.dataset.listingId}`);
      if (item.analysis?.status && item.analysis.status !== descriptionPanel.dataset.status) location.reload();
    } catch (_) { /* A temporary failure should leave the advert readable. */ }
  }, 20000);
}

const findingDialog = document.querySelector('#finding-dialog');
if (findingDialog) {
  let selectedHighlight;
  document.querySelectorAll('.description-highlight').forEach(highlight => {
    const showDecision = () => {
      selectedHighlight = highlight;
      const content = document.querySelector('#finding-dialog-content');
      content.replaceChildren();
      JSON.parse(highlight.dataset.decisions).forEach(decision => {
        const section = node('section', null, 'finding-decision');
        section.append(node('h3', decision.label));
        section.append(node('p', decision.key === 'drivable' ? (decision.value ? 'Decision: Drivable' : 'Decision: Not drivable') : (decision.value ? 'Decision: Yes' : 'Decision: No — explicitly denied'), 'finding-decision-value'));
        section.append(node('blockquote', decision.evidence));
        content.append(section);
      });
      findingDialog.showModal();
    };
    highlight.addEventListener('click', showDecision);
    highlight.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        showDecision();
      }
    });
  });
  document.querySelector('#close-finding-dialog').addEventListener('click', () => findingDialog.close());
  findingDialog.addEventListener('click', event => {
    const rect = findingDialog.getBoundingClientRect();
    if (event.target === findingDialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) findingDialog.close();
  });
  findingDialog.addEventListener('close', () => selectedHighlight?.focus());
}

// Native dialogs provide Escape-to-close and keep keyboard focus in the preview.
const imagePreview = document.querySelector('#image-preview');
if (imagePreview) {
  const photo = document.querySelector('#image-preview-photo');
  document.querySelectorAll('.gallery-preview').forEach(button => {
    button.addEventListener('click', () => {
      const thumbnail = button.querySelector('img');
      photo.src = thumbnail.src;
      photo.alt = thumbnail.alt;
      imagePreview.showModal();
    });
  });
  document.querySelector('#close-image-preview').addEventListener('click', () => imagePreview.close());
  imagePreview.addEventListener('click', event => {
    const rect = imagePreview.getBoundingClientRect();
    if (event.target === imagePreview && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) imagePreview.close();
  });
  imagePreview.addEventListener('close', () => photo.removeAttribute('src'));
}

const settingsForm = document.querySelector('#settings-form');
if (settingsForm) {
  settingsForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = settingsForm.querySelector('[type=submit]');
    const message = document.querySelector('#settings-message');
    button.disabled = true;
    try {
      await api('/api/settings', {method: 'PUT', body: JSON.stringify({refresh_start_hours: [...settingsForm.querySelectorAll('[name="refresh_start_hours"]:checked')].map(input => Number(input.value))})});
      message.textContent = 'Settings saved. The schedule updates within one minute.';
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
  const checkButton = document.querySelector('#check-ollama');
  async function checkOllama() {
    const status = document.querySelector('#ollama-status');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    checkButton.disabled = true;
    status.dataset.state = 'checking';
    status.textContent = 'Checking Ollama…';
    try {
      const result = await api('/api/settings/ollama', {signal: controller.signal, cache: 'no-store'});
      status.dataset.state = result.ready ? 'ready' : 'error';
      status.textContent = `${result.ready ? 'Ready' : 'Not ready'} — ${result.message}`;
    } catch (error) {
      status.dataset.state = 'error';
      status.textContent = error.name === 'AbortError'
        ? 'Check timed out. Check the app connection and try again.'
        : `Check failed: ${error.message}`;
    } finally {
      clearTimeout(timeout);
      checkButton.disabled = false;
    }
  }
  checkButton.addEventListener('click', checkOllama);
  checkOllama();
}

// Keep dashboard analysis indicators current while the background worker runs.
const llmIndicators = document.querySelectorAll('[data-llm-search]');
const modelLlmWaiting = document.querySelector('#model-llm-waiting');
if (llmIndicators.length || modelLlmWaiting) {
  let updatingIndicators = false;
  async function updateLlmIndicators() {
    if (document.hidden || updatingIndicators) return;
    updatingIndicators = true;
    try {
      const status = await api('/api/status');
      if (modelLlmWaiting) {
        const analysis = status.description_analysis;
        modelLlmWaiting.hidden = !analysis.enabled || !(analysis.pending || analysis.errors);
      }
      status.searches.forEach(search => {
        const indicator = document.querySelector(`[data-llm-search="${search.id}"]`);
        if (!indicator) return;
        const label = search.llm_label;
        if (indicator.getAttribute('aria-label') !== label) indicator.setAttribute('aria-label', label);
        indicator.dataset.state = search.llm_state;
      });
    } catch (_) { /* Keep the last known indicator during a connection failure. */ }
    finally { updatingIndicators = false; }
  }
  updateLlmIndicators();
  setInterval(updateLlmIndicators, 10000);
}
