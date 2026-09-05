const CACHE_NAME = 'ultratech-pwa-v3';

const PRECACHE_ASSETS = [
    '/',
    '/offline',
    '/static/manifest.json',
    '/static/favicon.ico',
    '/static/img/icons/icon-192x192.png',
    '/static/img/icons/icon-512x512.png',
    '/static/img/ultratech.jpg',
    'https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,400;1,600&family=Orbitron:ital,wght@0,400;0,600;0,700;0,800;0,900;1,700;1,800;1,900&display=swap',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css'
];

// Instalación: Precarga de recursos clave
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                return cache.addAll(PRECACHE_ASSETS).catch((err) => {
                    console.warn('[SW] Aviso: Algunos recursos precacheados fallaron (continuando):', err);
                });
            })
            .then(() => self.skipWaiting())
    );
});

// Activación: Limpieza de versiones obsoletas de caché
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME) {
                        console.log('[SW] Eliminando caché antiguo:', cache);
                        return caches.delete(cache);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Estrategia de Fetch
self.addEventListener('fetch', (event) => {
    // 1. Ignorar solicitudes que no sean GET (como POST de ventas, arqueos, etc.)
    if (event.request.method !== 'GET') {
        return;
    }

    const url = new URL(event.request.url);

    // 2. Rutas dinámicas sensibles que NO deben ser cacheadas (autenticación, cierres, endpoints api)
    if (url.pathname.startsWith('/auth/') || url.pathname.includes('logout') || url.pathname.startsWith('/api/')) {
        event.respondWith(fetch(event.request));
        return;
    }

    // 3. Manejo de Navegación HTML (Páginas): Network First con Fallback a Offline
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request)
                .then((networkResponse) => {
                    // Actualizar caché de la página si fue exitosa
                    if (networkResponse && networkResponse.status === 200) {
                        const responseClone = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, responseClone);
                        });
                    }
                    return networkResponse;
                })
                .catch(async () => {
                    // Si no hay red, intentar obtener de caché
                    const cachedResponse = await caches.match(event.request);
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    // Fallback a pantalla offline
                    const offlinePage = await caches.match('/offline');
                    if (offlinePage) {
                        return offlinePage;
                    }
                    return new Response('Sin conexión a internet', {
                        status: 503,
                        statusText: 'Service Unavailable',
                        headers: new Headers({ 'Content-Type': 'text/plain' })
                    });
                })
        );
        return;
    }

    // 4. Recursos Estáticos (Imágenes, Fuentes, CSS, JS): Stale While Revalidate
    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            const fetchPromise = fetch(event.request)
                .then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200) {
                        const responseClone = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, responseClone);
                        });
                    }
                    return networkResponse;
                })
                .catch(() => {
                    // Error de red silencioso para assets secundarios
                });

            return cachedResponse || fetchPromise;
        })
    );
});
