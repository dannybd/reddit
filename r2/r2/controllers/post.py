# The contents of this file are subject to the Common Public Attribution
# License Version 1.0. (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://code.reddit.com/LICENSE. The License is based on the Mozilla Public
# License Version 1.1, but Sections 14 and 15 have been added to cover use of
# software over a computer network and provide for limited attribution for the
# Original Developer. In addition, Exhibit A has been modified to be consistent
# with Exhibit B.
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License for
# the specific language governing rights and limitations under the License.
#
# The Original Code is reddit.
#
# The Original Developer is the Initial Developer.  The Initial Developer of
# the Original Code is reddit Inc.
#
# All portions of the code written by reddit are Copyright (c) 2006-2015 reddit
# Inc. All Rights Reserved.
###############################################################################
from r2.lib.pages import *
from reddit_base import (
    hsts_eligible,
    hsts_modify_redirect,
)
from api import ApiController
from r2.lib.errors import BadRequestError, errors
from r2.lib.utils import Storage, query_string, UrlParser
from r2.lib.emailer import opt_in, opt_out
from r2.lib.validator import *
from r2.lib.validator.preferences import (
    filter_prefs,
    PREFS_VALIDATORS,
    set_prefs,
)
from r2.lib.csrf import csrf_exempt
from r2.models.recommend import ExploreSettings
from pylons import request, c, g
from pylons.controllers.util import redirect_to
from pylons.i18n import _
from r2.models import *
import hashlib
from r2.lib.base import abort

class PostController(ApiController):
    @csrf_exempt
    @validate(pref_lang = VLang('lang'),
              all_langs = VOneOf('all-langs', ('all', 'some'), default='all'))
    def POST_unlogged_options(self, all_langs, pref_lang):
        prefs = {"pref_lang": pref_lang}
        set_prefs(c.user, prefs)
        c.user._commit()
        return self.redirect(request.referer)

    @validate(VUser(), VModhash(),
              all_langs=VOneOf('all-langs', ('all', 'some'), default='all'),
              **PREFS_VALIDATORS)
    def POST_options(self, all_langs, **prefs):
        filter_prefs(prefs, c.user)
        if c.errors.errors:
            return abort(BadRequestError(errors.INVALID_PREF))
        set_prefs(c.user, prefs)
        c.user._commit()
        u = UrlParser(c.site.path + "prefs")
        u.update_query(done = 'true')
        if c.cname:
            u.put_in_frame()
        return self.redirect(u.unparse())

    def GET_over18(self):
        return BoringPage(_("over 18?"), content=Over18(),
                          show_sidebar=False).render()

    @validate(VModhash(fatal=False),
              over18 = nop('over18'),
              dest = VDestination(default = '/'))
    def POST_over18(self, over18, dest):
        if over18 == 'yes':
            if c.user_is_loggedin and not c.errors:
                c.user.pref_over_18 = True
                c.user._commit()
            else:
                c.cookies.add("over18", "1")
            return self.redirect(dest)
        else:
            return self.redirect('/')


    @csrf_exempt
    @validate(msg_hash = nop('x'))
    def POST_optout(self, msg_hash):
        email, sent = opt_out(msg_hash)
        if not email:
            return self.abort404()
        return BoringPage(_("opt out"),
                          content = OptOut(email = email, leave = True,
                                           sent = True,
                                           msg_hash = msg_hash)).render()

    @csrf_exempt
    @validate(msg_hash = nop('x'))
    def POST_optin(self, msg_hash):
        email, sent = opt_in(msg_hash)
        if not email:
            return self.abort404()
        return BoringPage(_("welcome back"),
                          content = OptOut(email = email, leave = False,
                                           sent = True,
                                           msg_hash = msg_hash)).render()

    @validate(dest=VDestination(default='/'))
    def GET_shib_login(self, dest):
        def get_valid_local_part(email):
            if not email or len(email.split('@')) != 2:
                return False
            local, domain = email.split('@')
            if domain != 'mit.edu':
                return False
            return local
        def parse_GET(query_string):
            GET = {}
            args = query_string.split('&')
            for arg in args:
                t = arg.split('=')
                if len(t) != 2:
                    continue
                k, v = t
                GET[k] = v
            return GET
        test = {}
        user = get_valid_local_part(request.environ['HTTP_REMOTE_USER'])
        affiliation = request.environ['HTTP_AFFILIATION']
        if affiliation and len(affiliation.split(';')) > 1:
            affiliation = affiliation.split(';')[0]
        affiliation = get_valid_local_part(affiliation)
        GET = parse_GET(request.environ['QUERY_STRING'])
        # destination = '%2F'
        # if 'dest' in GET:
        #     destination = GET['dest']
        # destination = unquote(destination).decode('utf8')
        ApiController._handle_shib_login(self, user, affiliation)
        return self.redirect(dest)

    def GET_login_check(self, *a, **kw):
        if not c.user_is_loggedin:
            abort(403)
        return "200 OK Logged In"

    @validate(dest = VDestination(default = "/"))
    def GET_login_required(self, dest, *a, **kw):
        return BoringPage(_("login please"), content="You need to login",
                         show_sidebar=False).render()

    @csrf_exempt
    @validate(dest = VDestination(default = "/"))
    def POST_login(self, dest, *a, **kw):
        ApiController._handle_login(self, *a, **kw)
        c.render_style = "html"
        response.content_type = "text/html"

        if c.errors:
            return LoginPage(user_login = request.POST.get('user'),
                             dest = dest).render()

        return self.hsts_redirect(dest)

    @csrf_exempt
    @validate(dest = VDestination(default = "/"))
    def POST_reg(self, dest, *a, **kw):
        ApiController._handle_register(self, *a, **kw)
        c.render_style = "html"
        response.content_type = "text/html"

        if c.errors:
            return LoginPage(user_reg = request.POST.get('user'),
                             dest = dest).render()

        return self.hsts_redirect(dest)

    def GET_login(self, *a, **kw):
        return self.redirect('/login' + query_string(dict(dest="/")))

    @validatedForm(
        VUser(),
        VModhash(),
        personalized=VBoolean('pers', default=False),
        discovery=VBoolean('disc', default=False),
        rising=VBoolean('ris', default=False),
        nsfw=VBoolean('nsfw', default=False),
    )
    def POST_explore_settings(self,
                              form,
                              jquery,
                              personalized,
                              discovery,
                              rising,
                              nsfw):
        ExploreSettings.record_settings(
            c.user,
            personalized=personalized,
            discovery=discovery,
            rising=rising,
            nsfw=nsfw,
        )
        return redirect_to(controller='front', action='explore')
