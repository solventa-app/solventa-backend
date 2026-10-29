// Página de pago del stub de la pasarela (W03-28). Sin librerías ni CDN.
// La tarjeta solo viaja al mismo origen (POST /checkout/{id}/pagar) y nunca se guarda en el navegador.
(function () {
  'use strict'
  var datos = JSON.parse(document.body.dataset.pagina || '{}')
  var t = datos.textos || {}
  var formulario = document.getElementById('formulario')
  var cuenta = document.getElementById('cuenta')
  var DURACION_MINIMA_MS = 2200 // la animación de «Pagando…» dura de 2 a 3 s

  function mostrar(id, visible) {
    var nodo = document.getElementById(id)
    if (nodo) nodo.hidden = !visible
  }

  function resultado(titulo, texto) {
    document.getElementById('resultado-titulo').textContent = titulo
    document.getElementById('resultado-texto').textContent = texto
    mostrar('formulario', false)
    mostrar('pagando', false)
    mostrar('resultado', true)
    document.getElementById('regresar').focus()
  }

  if (cuenta && datos.venceEn) {
    var vence = new Date(datos.venceEn).getTime()
    var tic = function () {
      var restante = Math.max(0, Math.floor((vence - Date.now()) / 1000))
      var m = Math.floor(restante / 60)
      var s = restante % 60
      cuenta.textContent = m + ':' + (s < 10 ? '0' : '') + s
      if (restante === 0) {
        clearInterval(intervalo)
        if (formulario && !formulario.hidden) resultado(t.vencida, t.vencida_texto)
      }
    }
    var intervalo = setInterval(tic, 1000)
    tic()
  }

  if (!formulario) return

  formulario.addEventListener('submit', function (evento) {
    evento.preventDefault()
    var campos = formulario.elements
    var cuerpo = {
      tipo: (formulario.querySelector('input[name="tipo"]:checked') || {}).value,
      numero: campos.numero.value,
      nombre: campos.nombre.value,
      vencimiento: campos.vencimiento.value,
      cvv: campos.cvv.value,
    }
    mostrar('error', false)
    mostrar('formulario', false)
    mostrar('pagando', true)
    var inicio = Date.now()
    var ruta = window.location.pathname.replace(/\/$/, '') + '/pagar'
    fetch(ruta, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cuerpo),
      credentials: 'omit',
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { status: r.status, cuerpo: j }
        })
      })
      .then(function (respuesta) {
        var espera = Math.max(0, DURACION_MINIMA_MS - (Date.now() - inicio))
        setTimeout(function () {
          var c = respuesta.cuerpo || {}
          if (respuesta.status === 422 && c.campo) {
            mostrar('pagando', false)
            mostrar('formulario', true)
            var error = document.getElementById('error')
            error.textContent = t['campo.' + c.campo] || t.error_red
            error.hidden = false
            return
          }
          if (c.estado === 'COBRADA') return resultado(t.aprobado, t.aprobado_texto)
          if (c.estado === 'RECHAZADA') return resultado(t.rechazado, t['motivo.' + c.motivo] || '')
          if (c.estado === 'VENCIDA') return resultado(t.vencida, t.vencida_texto)
          resultado(t.fallido, t.fallido_texto)
        }, espera)
      })
      .catch(function () {
        resultado(t.fallido, t.error_red)
      })
    // La tarjeta no se queda en el formulario ni en memoria de la página.
    formulario.reset()
  })
})()
