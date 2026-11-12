import {createRouter, createWebHistory} from 'vue-router'
import ReportPage from './pages/ReportPage.vue'
import ReportsPage from './pages/ReportsPage.vue'
import RequirementsPage from './pages/RequirementsPage.vue'

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
        {
            path: '/reports/:reportId(\\d+)/requirements',
            name: 'requirements',
            component: RequirementsPage,
            props: route => ({reportId: Number(route.params.reportId)}),
        },
        {path: '/:pathMatch(.*)*', redirect: '/'},
    ],
})
