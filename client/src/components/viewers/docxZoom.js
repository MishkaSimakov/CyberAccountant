// Runs inside the isolated DOCX frame, where trackpad events originate.
(() => {
    const content = document.getElementById('docx-content')
    const viewport = document.getElementById('docx-viewport')
    const stage = document.getElementById('docx-stage')
    if (!content || !viewport || !stage) return

    let scale = 1
    let safariGestureScale

    function updateExtent() {
        content.style.transform = `scale(${scale})`
        stage.style.width = `${Math.max(content.offsetWidth, content.scrollWidth) * scale}px`
        stage.style.height = `${Math.max(content.offsetHeight, content.scrollHeight) * scale}px`
    }

    function fitWidth() {
        content.style.width = `${viewport.clientWidth}px`
        updateExtent()
    }

    function scaleAt(factor, x, y) {
        const next = Math.min(5, Math.max(0.25, scale * factor))
        if (!Number.isFinite(next) || next === scale) return
        const ratio = next / scale
        const bounds = viewport.getBoundingClientRect()
        x -= bounds.left
        y -= bounds.top
        const left = (viewport.scrollLeft + x) * ratio - x
        const top = (viewport.scrollTop + y) * ratio - y
        scale = next
        updateExtent()
        viewport.scrollTo(left, top)
    }

    viewport.addEventListener('wheel', event => {
        event.preventDefault()
        const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16
            : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? viewport.clientHeight : 1
        if (!event.ctrlKey) {
            // Pan the explicit scroll container in both axes, including momentum events.
            viewport.scrollBy(event.deltaX * unit, event.deltaY * unit)
            return
        }
        // Chromium and Firefox expose trackpad pinches as Ctrl+wheel.
        if (safariGestureScale !== undefined) return
        scaleAt(Math.exp(-event.deltaY * unit * 0.01), event.clientX, event.clientY)
    }, {passive: false})

    for (const name of ['gesturestart', 'gesturechange', 'gestureend']) {
        viewport.addEventListener(name, event => {
            // Safari exposes native gesture events instead of Ctrl+wheel.
            event.preventDefault()
            if (event.type === 'gesturestart') {
                safariGestureScale = event.scale
            } else if (event.type === 'gesturechange') {
                scaleAt(event.scale / (safariGestureScale ?? 1), event.clientX, event.clientY)
                safariGestureScale = event.scale
            } else {
                safariGestureScale = undefined
            }
        }, {passive: false})
    }

    fitWidth()
    new ResizeObserver(updateExtent).observe(content)
    window.addEventListener('resize', fitWidth)
})()
