#!/usr/bin/env python3

import sys
import os
import sqlite3
import threading

from pprint import pprint as pp
from dateutil import parser as date_parser
import click

from tvoverlord.shows import Shows
from tvoverlord.show import Show
from tvoverlord.config import Config
import tvoverlord.tvutil as tvu
from tvoverlord.location import Location
from tvoverlord.history import History
from tvoverlord.search import Search
from tvoverlord.calendar import calendar as Calendar
from tvoverlord.info import info as Info
from tvoverlord.db import DB
from tvoverlord.db import Database
from tvoverlord.remote import VersionCheck
from tvoverlord.remote import Telemetry


__version__ = '1.4.3'


def edit_db(search_str):
    try:
        import readline
    except ImportError:
        pass

    sql = 'SELECT * FROM shows WHERE name like :search'
    conn = sqlite3.connect(Config.db_file)
    conn.row_factory = tvu.dict_factory
    curs = conn.cursor()
    values = {'search': '%{}%'.format(search_str)}
    results = curs.execute(sql, values)

    data = []
    for i in results:
        data.append(i)

    if len(data) == 0:
        click.echo('"%s" not found' % search_str)
        sys.exit()
    elif len(data) > 1:
        click.echo('Multiple shows found, type a number to edit.')
        click.echo('Type "<ctrl> c" to cancel.')
        click.echo()
        for index, show in enumerate(data):
            click.echo('  %s. %s' % (index + 1, show['name']))
        click.echo()
        choice = click.prompt(
            'Choose number', default=1,
            type=click.IntRange(min=1, max=len(data)))
        idchoice = choice - 1
        if idchoice not in range(len(data)):
            sys.exit('Invalid choice: %s' % choice)
        row = data[idchoice]
    else:
        row = data[0]

    editcolor = 'green' if Config.is_win else 31
    dirty = False
    is_error = False

    click.echo()
    click.echo(tvu.format_paragraphs('''
      While editing a field, hit <enter> in an empty field to leave it
      unchanged and skip to the next one.  Type "<ctrl> c" to cancel all
      edits.  The current value is shown in ()\'s beside the field
      name.'''))
    click.echo()

    title = '%s' % row['name']
    click.echo(tvu.style(title, bold=True))
    click.echo()
    try:
        msg = tvu.style('Search engine name (%s): ', fg=editcolor)
        new_search_engine_name = input(msg % (row['search_engine_name']))
        if not new_search_engine_name:
            new_search_engine_name = row['search_engine_name']
        else:
            dirty = True

        msg = tvu.style('Current season (%s): ', fg=editcolor)
        new_season = input(msg % (row['season']))
        if not new_season:
            new_season = str(row['season'])
        else:
            dirty = True

        msg = tvu.style('Last episode (%s): ', fg=editcolor)
        new_episode = input(msg % (row['episode']))
        if not new_episode:
            new_episode = str(row['episode'])
        else:
            dirty = True

        msg = tvu.style('Status (%s): ', fg=editcolor)
        new_status = input(msg % (row['status']))
        if not new_status:
            new_status = row['status']
        else:
            dirty = True

    except KeyboardInterrupt:
        click.echo('\nDatabase edit canceled.')
        sys.exit(0)

    if dirty is False:
        click.echo('No changes made.')
        sys.exit(0)

    if not new_season.isdigit():
        click.echo('Error: Season must be a number')
        is_error = True

    if not new_episode.isdigit():
        click.echo('Error: Episode must be a number')
        is_error = True

    if new_status not in ['active', 'inactive']:
        click.echo('Error: Status must be either "active" or "inactive"')
        is_error = True

    if is_error:
        sys.exit(1)

    click.echo()

    if not click.confirm('Are these changes correct? (you can always change it back)', default='Y'):
        click.echo('Edits cancelled.')
        sys.exit()

    sql = '''UPDATE shows SET
                season=:season,
                episode=:episode,
                status=:status,
                search_engine_name=:search_engine_name
             WHERE thetvdb_series_id=:tvdb_id'''

    row_values = {'season': new_season,
                  'episode': new_episode,
                  'status': new_status,
                  'search_engine_name': new_search_engine_name,
                  'tvdb_id': row['thetvdb_series_id']}

    curs.execute(sql, row_values)

    conn.commit()
    conn.close()


def send(te, v):
    """Asynchronously send telemetry data to remote server"""

    ctx = click.get_current_context()
    params = ctx.params
    params.update(ctx.parent.params)
    params['command'] = ctx.info_name
    try:
        # don't collect the show name
        if params['show_name']:
            params['show_name'] = True
    except KeyError:
        pass

    try:
        # don't collect the config name
        if params['config_name']:
            params['config_name'] = True
    except KeyError:
        pass

    commands = '|'.join(['%s;%s' % (i, params[i]) for i in params])

    def send_to_remote():
        db2 = Database()
        db2.configure()
        if Config.version_notification:
            # Get any new version messages while we are
            # in a seperate thread.
            v.get_version(db=db2)
        if te.have_permission(db=db2):
            # And send the telemetry data.
            te.send(db=db2, cmd=commands, version=__version__)

    th = threading.Thread(target=send_to_remote, group=None)
    th.start()


CONTEXT_SETTINGS = {
    # add -h in addition to --help
    'help_option_names': ['-h', '--help'],
    # allow case insensitive commands
    'token_normalize_func': lambda x: x.lower(),
}

te = Telemetry()
v = VersionCheck(__version__)

@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--no-cache', '-n', is_flag=True,
              help='Re-download the show data instead of using\nthe cached data.')
@click.option('--config', 'config_name', default=None,
              help='Alternative config name to use.')
@click.version_option(version=__version__)
def tvol(no_cache, config_name):
    """Download and manage tv shows.

    Use `tvol COMMAND -h` to get help for each command.

    \b
       \/    TVOverlord source code is available at:
      [. ]   https://github.com/8cylinder/tv-overlord
       ^
      /^\\    Any feature requests or bug reports should go there.
     //^\\\\
    -^-._.--.-^^-.____._^-.^._
    """
    Config.get_config_data(config_name)
    DB.configure()

    if Config.version_notification:
        if v.new_version():
            msg = tvu.style(v.message, fg='green')
            click.secho(msg)

    te.ask()

    if no_cache:
        Config.use_cache = False
    else:
        Config.use_cache = True


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('show_name', required=False)
@click.option('--show-all', '-a', is_flag=True,
              help='Show all shows including ones that don\'t have upcoming episodes.')
@click.option('--status', type=click.Choice(['active', 'inactive', 'all']),
              default='active',
              help='Display shows with this status.')
@click.option('--sort-by-next', '-x', is_flag=True,
              help='Sort by release date instead of the default alphabetical.')
@click.option('--ask-inactive', is_flag=True,
              help='Ask to make inactive shows that are cancelled.')
@click.option('--show-links', is_flag=True,
              help='Show links to IMDB.com and TheTVDb.com for each show.')
@click.option('--synopsis', is_flag=True,
              help='Display the show synopsis.')
def info(show_name, show_all, sort_by_next, status,
         ask_inactive, show_links, synopsis):
    """Show information about your tv shows.

    Info without any options, shows all shows with upcoming episodes.  If
    --show-all is used, it will include shows that have no upcoming episodes.

    Info's default is to only show shows that are active, but --status can
    be used to change that.

    SHOW_NAME can be a full or partial name of a show.  If used, it
    will show information about any shows that match that string, else
    it will show informaton about all your shows.
    """
    send(te, v)

    db_status = status
    Info(show_name, show_all, sort_by_next, db_status,
         ask_inactive, show_links, synopsis)


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('show_name', required=False)
@click.option('--show-all', '-a', is_flag=True,
              help='Show all shows including the ones marked inactive.')
@click.option('--sort-by-next', '-x', is_flag=True,
              help='Sort by release date instead of the default alphabetical.')
@click.option('--no-color', is_flag=True,
              help="Don't use color in output.  Useful if output is to be used in email or text file.")
@click.option('--days',
              help='The number of days to show in the calendar.')
def calendar(show_name, show_all, sort_by_next, no_color, days):
    """Display a calendar of upcoming episodes.

    If SHOW_NAME is used, it will display a calendar for any shows that
    match that string.

    --days can be one number or two numbers.  If one number is used, it will
    show that many days ahead.  If two numbers are used, the first number is
    where the calendar will start and the second number is where it will end.
    The two number must be seperated by a comma with no spaces.

    \b
    --days 10      will show from today to 10 days in the future
    --days 10,20   will start ten days from now and then show 20 days ahead.
    --days -20,10  will go back 20 days from today and then show ahead from there.
    """
    send(te, v)
    Calendar(show_name, show_all, sort_by_next, no_color, days)


def tfunct(series):
    """Display the series title on the right side of the progressbar"""
    try:
        title = series.db_name
    except AttributeError:
        title = ''
    return title


@tvol.command('list', context_settings=CONTEXT_SETTINGS)
@click.option('--today', '-t', is_flag=True,
              help="Also show today's episodes.")
def list_missing(today):
    """List episodes that are ready to download.
    """
    send(te, v)
    fp = tvu.FancyPrint()

    shows = Shows()

    with click.progressbar(
            shows,
            item_show_func=tfunct,
            show_percent=False,
            show_eta=False,
            width=Config.pb.width,
            empty_char=tvu.style(Config.pb.empty_char,
                             fg=Config.pb.dark,
                             bg=Config.pb.dark),
            fill_char=tvu.style(Config.pb.fill_char,
                            fg=Config.pb.light,
                            bg=Config.pb.light),
            bar_template=Config.pb.template,
    ) as bar:
        for series in bar:
            if series.is_missing(today):
                fp.standard_print(series.show_missing())
    fp.done()


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('show_name', required=False)
@click.option('--today', '-t', is_flag=True,
              help="Also download today's episodes.")
@click.option('--ignore', '-i', is_flag=True,
              help="Ignore 'Not connected to vpn' warning.")
@click.option('--count', '-c', type=int, default=10,
              help='Number of search results to list. (default: 5)')
def download(show_name, today, ignore, count):
    """Download available episodes.

    If SHOW_NAME is used, it will download any shows that match that title
    """
    send(te, v)
    if not ignore and (Config.email or Config.ip):
        L = Location()
        if Config.email:
            if not L.getipintel():
                warning = click.style(
                    'Warning:', bg='red', fg='white', bold=True)
                msg = '{warning} not connected to a VPN'
                click.echo(msg.format(warning=warning))
                sys.exit(1)
        if Config.ip:
            if not L.ips_match(
                    Config.ip,
                    parts_to_match=Config.parts_to_match):
                L.message()
                sys.exit(1)

    shows = Shows(name_filter=show_name)
    for show in shows:
        show.download_missing(count, today)


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('show_name')
@click.option('--bulk', is_flag=True,
              help='Bypass dialogs, no checks are done.')
@click.option('--season', '-s', default=0,
              help='Set the season when using --bulk.')
@click.option('--episode', '-e', default=0,
              help='Set the episode when using --bulk.')
def add(show_name, bulk, season, episode):
    """Add a new tv show to the database.

    The SHOW_NAME can be a partial name, but the more accurate the name
    the better the search will be.  It helps to add the year to the name
    as well.

    If you search for 'Doctor Who', the result will be the original series,
    but if you want the modern one, search for 'Doctor Who 2005'

    If bulk is used, then season and episode can be used.
    """
    send(te, v)
    if not show_name:
        raise click.UsageError('Empty "show_name" not allowed.')

    new_show = Show(show_type='new')
    if bulk == True:
        new_show.add_bulk(show_name, season=season, episode=episode)
    else:
        new_show.add_new(name=show_name)


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('search_string')
@click.option('--count', '-c', type=int, default=10,
              help='Number of search results to list. (default: 5)')
@click.option('--ignore', '-i', is_flag=True,
              help="Ignore 'Not connected to vpn' warning.")
def nondbshow(search_string, count, ignore):
    """Download anything, ignoring the database.

    This just does a simple search and passes you choise to the bittorrent
    client.  The download is not recorded in the database.
    """
    send(te, v)
    if not search_string:
        raise click.UsageError('Empty "search_string" not allowed.')

    if not ignore and (Config.email or Config.ip):
        L = Location()
        if Config.email:
            if not L.getipintel():
                warning = click.style(
                    'Warning:', bg='red', fg='white', bold=True)
                msg = '{warning} not connected to a VPN'
                click.echo(msg.format(warning=warning))
                sys.exit(1)
        if Config.ip:
            if not L.ips_match(
                    Config.ip,
                    parts_to_match=Config.parts_to_match):
                L.message()
                sys.exit(1)

    nons = Show(show_type='nondb')
    nons.non_db(search_string, count)


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.option('--action',
              type=click.Choice(['delete', 'deactivate', 'activate']),
              help='Preform actions on a show.')
@click.argument('show_name')
def editshow(show_name, action):
    """Edit the contents of the database.

    This allows you to change the fields in the database for a show.
    The fields affected are:

    \b
    Search engine title:
        Sometimes a different name searches better.  If this
        is set, it will be used when searching.
    \b
    Current season:
        Setting this can be usefull if you add a new show to
        the db, but want to download starting at a later season.
    \b
    Last episode:
        Set this to change the last episode downloaded.
    \b
    Status:
        This can be 'active' or 'inactive'.  This can be used
        to turn off a show.
    """
    send(te, v)
    if not show_name:
        raise click.UsageError('Empty "search_string" not allowed.')

    if action:
        shows = Shows(show_name, status='all')
        shows = list(shows)
        if len(shows) == 1:
            msg = shows[0].edit(action)
        elif len(shows) > 1:
            click.echo('Multiple shows found, type a number to select.')
            click.echo('Type "<ctrl> c" to cancel.')
            click.echo()
            click.echo(tvu.itemize(shows))
            choice = click.prompt(
                'Choose number',
                type=click.IntRange(min=1, max=len(shows)))
            choice -= 1  # tvu.itemize starts at 1, not 0
            msg = shows[choice].edit(action)
        else:
            msg = 'No shows match: "%s"' % show_name

        click.echo(msg)
    else:
        edit_db(show_name)


def parse_history(criteria):
    # try to parse criteria as an int, then as a date.  If neither don't
    # work, pass it on as a string which should be a show title
    try:
        criteria = int(criteria)
    except ValueError:
        try:
            criteria = date_parser.parse(criteria)
        except ValueError:
            criteria = criteria
    return criteria


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('criteria', required=False)
@click.option('--what-to-show', '-w', type=str,
              help="An optional list of information to show seperated by commas.")
def history(criteria, what_to_show):
    """Show a list of downloaded episodes.

    CRITERIA can be days, a date or a show title.  If its days, it
    will show results from now to X days ago.  If it is a date, it
    will show downloads for that date, and if its a title or partial
    title, it will show all downloads for that show.

    \b
    what-to-show can be any of these:
    date, title, season, episode, magnet, oneoff, complete, filename, destination

    eg. --what-to-show 'title,filename,magnet'
    """
    send(te, v)
    if not criteria:
        criteria = 1
    criteria = parse_history(criteria)
    hist = History(criteria)
    hist.show(what_to_show)


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('criteria', required=False)
def copy(criteria):
    """Re copy a show to the library location.

    CRITERIA can be days, a date or a show title.  If its days, it
    will show results from now to X days ago.  If it is a date, it
    will show downloads for that date, and if its a title or partial
    title, it will show all downloads for that show.
    """
    send(te, v)
    if not criteria:
        criteria = 1
    criteria = parse_history(criteria)
    hist = History(criteria)
    hist.copy()


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.argument('criteria', required=False)
def redownload(criteria):
    """Re download a show.

    CRITERIA can be days, a date or a show title.  If its days, it
    will show results from now to X days ago.  If it is a date, it
    will show downloads for that date, and if its a title or partial
    title, it will show all downloads for that show.
    """
    send(te, v)
    if not criteria:
        criteria = 1
    criteria = parse_history(criteria)
    hist = History(criteria)
    hist.download()


@tvol.command(context_settings=CONTEXT_SETTINGS)
@click.option('--edit', '-e', is_flag=True,
              help="Edit config.ini with default editor.")
@click.option('--test-se', type=str,
              help="Test each search engine.")
@click.option('--show', is_flag=True,
              help='If using --test-se, show the results of each search.')
@click.option('--create', 'config_name', type=str,
              help='Create a config set.')
def config(edit, test_se, show, config_name):
    """tvol's config information.

    Show information of where various files are, (config.ini,
    database) and a list of the search engines and the url's they use.
    """
    send(te, v)

    if config_name:
        Config.create_config(config_name, create=True)
        return

    if edit:
        click.edit(filename=Config.user_config)
        return

    if test_se:
        search = Search()
        search.test_each(test_se, show)
        return

    import shutil

    title = 'green'
    bold = True
    ul = True

    # file locations
    click.echo()
    click.secho('File locations:', fg=title, bold=bold, underline=ul)
    click.echo()

    click.echo('config file:     %s' % Config.user_config)
    click.echo('Database file:   %s' % Config.db_file)
    click.echo('NZB staging dir: %s' % Config.staging)
    click.echo('TV dir:          %s' % Config.tv_dir)
    click.echo('Alt client:      %s' % Config.client)
    click.echo('Magnet dir:      %s' % Config.magnet_dir)
    click.echo('Template:        %s' % Config.template)

    click.echo()
    for script in ['tvol', 'transmission_done', 'deluge_done']:
        loc = shutil.which(script)
        script = script + ':'
        click.echo('%s %s' % (script.ljust(18), loc))

    # search engines
    click.echo()
    click.secho('Search engines:', fg=title, bold=bold, underline=ul)
    search = Search()
    engines_types = [search.torrent_engines, search.newsgroup_engines]
    for engines in engines_types:
        for engine in engines:
            click.echo()
            click.secho(engine.Provider.name, bold=True, nl=False)
            click.echo(' (%s)' % engine.Provider.shortname)
            for url in engine.Provider.provider_urls:
                click.echo('  %s' % url)

    # blacklisted search engines
    if Config.blacklist:
        click.echo()
        click.secho('Search engine blacklist:',
                    fg=title, bold=bold, underline=ul)
        click.echo()
        for bl in Config.blacklist:
            click.echo(bl)

    # ip addresses
    click.echo()
    click.secho('Ip address information:',
                fg=title, bold=bold, underline=ul)
    click.echo()

    l = Location()
    click.echo('Your public ip address:')
    click.secho('  %s' % l.ip, bold=True)
    if Config.ip:
        click.echo()
        click.echo('Your whitelisted ip addresses:')
        short = '.'.join(l.ip.split('.')[:Config.parts_to_match])
        for ip in Config.ip:
            color = None
            if ip.startswith(short):
                color = 'green'
            click.secho('  %s' % ip, fg=color)
