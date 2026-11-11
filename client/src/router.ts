import {createRouter, createWebHistory} from 'vue-router'
import ReportPage from './pages/ReportPage.vue'
import ReportsPage from './pages/ReportsPage.vue'

export default createRouter({
    history: createWebHistory(),
    routes: [
        {path: '/', name: 'reports', component: ReportsPage},
        {
            path: '/reports/:reportId(\\d+)',
            name: 'report',
            component: ReportPage,
            props: route => ({reportId: Number(route.params.reportId)}),
        },
        {path: '/:pathMatch(.*)*', redirect: '/'},
    ],
})
