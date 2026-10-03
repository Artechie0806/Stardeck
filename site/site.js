// Stardeck website: the waitlist form, the product video's pause button, the footer year.
(() => {
  const contact = document.body.dataset.contact || ''

  for (const el of document.querySelectorAll('[data-year]')) el.textContent = String(new Date().getFullYear())

  // The product video loops silently. People who ask for less motion get it paused; anyone can pause it.
  const video = document.getElementById('hero-video')
  const toggle = document.querySelector('.video-toggle')
  if (video && toggle) {
    const sync = () => { toggle.textContent = video.paused ? 'Play' : 'Pause' }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      video.removeAttribute('autoplay')
      video.pause()
    }
    toggle.addEventListener('click', () => (video.paused ? video.play() : video.pause()))
    video.addEventListener('play', sync)
    video.addEventListener('pause', sync)
    // No video (yet, or it failed to load): no button for it either.
    video.addEventListener('error', () => { toggle.hidden = true }, true)
    if (video.networkState === HTMLMediaElement.NETWORK_NO_SOURCE) toggle.hidden = true
    sync()
  }

  const form = document.getElementById('waitlist-form')
  if (!form) return
  const status = form.querySelector('.form-status')
  const button = form.querySelector('button[type=submit]')
  const say = (text, kind) => {
    status.textContent = text
    status.className = `form-status ${kind || ''}`
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault()
    const data = Object.fromEntries(new FormData(form))
    if (!form.email.checkValidity()) {
      say('Please enter a valid email address.', 'bad')
      form.email.focus()
      return
    }
    button.disabled = true
    say('Sending…')
    try {
      const res = await fetch('/api/public/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: data.email, name: data.name, company: data.company, role: data.role,
          dataSource: data.dataSource, useCase: data.useCase, website: data.website,
        }),
      })
      if (res.status === 202) {
        form.innerHTML = '<div class="thanks" role="status" tabindex="-1"><h3>You’re on the list.</h3>'
          + '<p>Thank you. We’ll email you an invitation as soon as there’s room. Meanwhile, try the '
          + '<a href="/demo">live demo</a>.</p></div>'
        form.querySelector('.thanks').focus()
        return
      }
      const body = await res.json().catch(() => ({}))
      say(res.status === 422 ? 'Please enter a valid email address.'
        : typeof body.detail === 'string' ? body.detail : 'Something went wrong. Please try again.', 'bad')
    } catch {
      say(`We couldn’t send that. Please try again${contact ? `, or email us at ${contact}` : ''}.`, 'bad')
    } finally {
      button.disabled = false
    }
  })
})()
