from django.conf.urls import patterns, url
from api.views import default, user, session, testplan, webui

urlpatterns = patterns('',
    url(r'^$', default.index),
    url(r'^version/$', default.version),
    url(r'^user/$', user.rest),
    url(r'^user/(\w+?)/$', user.rest),
    url(r'^session/$', session.rest),
    url(r'^session/(\d+?)/$', session.rest),
    url(r'^testplan/$', testplan.rest),
    url(r'^webui/authenticate/$', webui.authenticate),
)
