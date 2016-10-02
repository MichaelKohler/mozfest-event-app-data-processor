import argparse, os, sys, traceback
import github3
import gspread
import io
import json
import logging
import os
import requests
from datetime import datetime, timedelta
from logging.config import dictConfig
from oauth2client.client import SignedJwtAssertionCredentials
from helper import parseListFromEnvVar

# #####
# Look for optional environment variables.
# If not set, use default values.
# #####

# TARGET_DIR. Default value ''.
TARGET_DIR = os.environ['TARGET_DIR'] + "/" if 'TARGET_DIR' in os.environ and os.environ['TARGET_DIR'] is not '' else ''

# TARGET_BRANCHES. Default value ['gh-pages'].
TARGET_BRANCHES = parseListFromEnvVar(os.environ['TARGET_BRANCHES']) if 'TARGET_BRANCHES' in os.environ else ['gh-pages']

# FETCH_MULTIPLE_WORKSHEETS. Default value True.
FETCH_MULTIPLE_WORKSHEETS = os.environ['FETCH_MULTIPLE_WORKSHEETS'] if 'FETCH_MULTIPLE_WORKSHEETS' in os.environ else True

# MAKE_LOCAL_JSON. Default value True.
MAKE_LOCAL_JSON = os.environ['MAKE_LOCAL_JSON'] if 'MAKE_LOCAL_JSON' in os.environ else True

# COMMIT_JSON_TO_GITHUB. Default value False.
COMMIT_JSON_TO_GITHUB = True if 'COMMIT_JSON_TO_GITHUB' in os.environ and os.environ['COMMIT_JSON_TO_GITHUB'] == 'True' else False

# SESSIONS_WORKSHEETS_TO_FETCH. Default value [].
SESSIONS_WORKSHEETS_TO_FETCH = parseListFromEnvVar(os.environ['SESSIONS_WORKSHEETS_TO_FETCH']) if 'SESSIONS_WORKSHEETS_TO_FETCH' in os.environ else []

# PROMPT_BEFORE_COMMIT_TO_GITHUB. Default value False.
PROMPT_BEFORE_COMMIT_TO_GITHUB = True if 'PROMPT_BEFORE_COMMIT_TO_GITHUB' in os.environ and os.environ['PROMPT_BEFORE_COMMIT_TO_GITHUB'] == 'True' else False

# custom [category] label in source spreadsheet, e.g., for MozFest 2016 this spreadsheet column is called "space"
CUSTOM_CATEGORY_LABEL = os.environ['CUSTOM_CATEGORY_LABEL'] if 'CUSTOM_CATEGORY_LABEL' in os.environ and len(os.environ['CUSTOM_CATEGORY_LABEL']) > 0 else 'category'
# custom [tags] label in source spreadsheet, e.g., for MozFest 2016 this spreadsheet column is called "pathways"
CUSTOM_TAGS_LABEL = os.environ['CUSTOM_TAGS_LABEL'] if 'CUSTOM_TAGS_LABEL' in os.environ and len(os.environ['CUSTOM_TAGS_LABEL']) > 0 else 'tags'

GITHUB_CONFIG = {
    'TOKEN': os.environ['GITHUB_TOKEN'],
    'REPO_OWNER': os.environ['REPO_OWNER'],
    'REPO_NAME': os.environ['REPO_NAME'],
    'TARGET_DIR': TARGET_DIR,
    'TARGET_FILE': 'sessions.json',
    'TARGET_BRANCHES': TARGET_BRANCHES
}

GOOGLE_API_CONFIG = {
    'CLIENT_EMAIL': os.environ['GOOGLE_API_CLIENT_EMAIL'],
    'PRIVATE_KEY': os.environ['GOOGLE_API_PRIVATE_KEY'].decode('unicode_escape'),
    'SCOPE': ['https://spreadsheets.google.com/feeds']
}

GOOGLE_SPREADSHEET_KEY = os.environ['GOOGLE_SPREADSHEET_KEY']

def authenticate_with_google():
    '''
    Connect to Google Spreadsheet with gspread library.
    '''
    credentials = SignedJwtAssertionCredentials(
        GOOGLE_API_CONFIG['CLIENT_EMAIL'], GOOGLE_API_CONFIG['PRIVATE_KEY'], GOOGLE_API_CONFIG['SCOPE']
    )
    google_api_conn = gspread.authorize(credentials)
    
    return google_api_conn
    
def open_google_spreadsheet():
    '''
    Authenticate and return spreadsheet by `GOOGLE_SPREADSHEET_KEY`.
    '''
    google_api_conn = authenticate_with_google()
    spreadsheet = google_api_conn.open_by_key(GOOGLE_SPREADSHEET_KEY)
    
    return spreadsheet

def fetch_data(multiple_sheets=False, sessions_worksheets_to_fetch=[]):
    spreadsheet = open_google_spreadsheet()

    data = { 
        'timeblocks': fetch_worksheets(spreadsheet, multiple_sheets, ['* Timeblock Values']),
        'sessions': fetch_worksheets(spreadsheet, multiple_sheets, sessions_worksheets_to_fetch) 
    }

    return data

def fetch_worksheets(spreadsheet, multiple_sheets=False, sessions_worksheets_to_fetch=[]):
    if not multiple_sheets:
        # Return data from first worksheet in Google spreadsheet.
        worksheet = spreadsheet.get_worksheet(0)
        data = worksheet.get_all_records(empty2zero=False)
    else:
        # Return data from all worksheets in sessions_worksheets_to_fetch
        data = []

        worksheet_list = [
            sheet for sheet in spreadsheet.worksheets() if sheet.title in sessions_worksheets_to_fetch
        ]

        for worksheet in worksheet_list:
            worksheet.title
            data.extend(worksheet.get_all_records(empty2zero=False))

    return data 

def slugify_timeblock(timeblock):
    # "slugified" version of timeblock
    timeblock = timeblock.strip().lower().replace(' ','-').replace(',','').replace(':','-').replace('*','').replace('&','-').replace('(','-').replace(')','-')
    return timeblock

def transform_timeblock_data(data):
    def _transform_response_item(item, skip=False):

        # make sure vars are strings
        _transformed_item = {k: unicode(v) for k, v in item.iteritems() if k}
        # remove rows that are blank or used for providing instructions
        if _transformed_item['day'] and _transformed_item['day'].find('select from dropdown') == -1 and _transformed_item['start time']:
            skip = False
        else:
            skip = True

        # transform `Auto Generated. Do Not Modify.` column name into `key` key
        if 'Auto Generated. Do Not Modify.' in _transformed_item:
            _transformed_item['key'] = slugify_timeblock(_transformed_item.pop('Auto Generated. Do Not Modify.', ''))
    
        # if we've triggered the skip flag anywhere, drop this record
        if skip:
            _transformed_item = None
            
        return _transformed_item

    def _add_timeblock_order(item, index, skip=False):
        # make sure vars are strings
        _transformed_item = {k: unicode(v) for k, v in item.iteritems() if k}
        # +1 because we want order to start from 1
        _transformed_item['order'] = index+1
        return _transformed_item

    dayOrder = {
        'Monday': 1,
        'Tuesday': 2,
        'Wednesday': 3,
        'Thursday': 4,
        'Friday': 5,
        'Saturday': 6,
        'Sunday': 7
    }

    def dayComparator(x, y):
        if dayOrder[x['day']] <  dayOrder[y['day']]:
            return -1
        elif dayOrder[x['day']] > dayOrder[y['day']]:
            return 1
        else:
            return 0

    # pass data through the transformer
    transformed_data = filter(None, [_transform_response_item(item) for item in data])
    # sort timeblocks by start time in a day
    transformed_data = sorted(transformed_data, key=lambda timeblock:(timeblock['start time']))
    # sort timeblocks again by day in week
    transformed_data.sort(dayComparator)
    # assign 'order' to timeblock
    transformed_data = filter(None, [_add_timeblock_order(item,index) for index, item in enumerate(transformed_data)])

    return transformed_data

def transform_session_data(data):
    '''
    Transforms data and filters individual schedule items for fields we want
    to publish. Currently, this:
    
    * ensures that all variables going into the JSON are strings
    * removes `proposalSpreadsheetRowNumber` to make JSON smaller
    * transforms column name `name` into JSON key `title`
    * transforms column name `id` into JSON key `id`
    * removes any rows that don't have a numeric `id`
    * creates a `facilitators` dict
    * creates a `facilitators_names` name list
    * transform column name "CUSTOM_CATEGORY_LABEL" into JSON key `category`
    * remove invalid tag labels that were used for GitHub workflow and transform column name "CUSTOM_TAGS_LABEL" into JSON key `tags`
    * creates a `timeblock` key based on data in `time` column
    * creates Saturday and Sunday versions of sessions marked 'all-weekend'
    * infers a `day` and `start` key based on data in `time` column
    * labels `programmatic` session
    '''
    def _transform_response_item(item, skip=False):
        # make sure vars are strings
        _transformed_item = {k: unicode(v) for k, v in item.iteritems() if k}
        
        # transform `name` column name into `title` key
        if 'name' in _transformed_item:
            _transformed_item['title'] = _transformed_item.pop('name', '')
            if not _transformed_item['title']:
                skip = True
        
        # set `id` key
        # (and skip rows without a valid id)
        if 'session id' in _transformed_item:
            _transformed_item['id'] = _transformed_item.pop('session id', '')

            # remove rows with `session id` that is blank or provides instructions
            if len(_transformed_item['id'].split(" ")) != 1:
                skip = True
        
        # create `facilitators` key
        facilitators = {}
        facilitators_names = []
        for key in _transformed_item.keys():
            if key.startswith('facilitator'):
                facilitatorObj = {}
                wordList = key.split(" ")
                facilitatorNumber = wordList[1]
                metaType = wordList[2]
                metaValue = _transformed_item.pop(key)

                if facilitatorNumber not in facilitators:
                    facilitators[facilitatorNumber] = facilitatorObj
                else:
                    facilitatorObj = facilitators[facilitatorNumber]

                if metaType == 'name':
                    facilitatorObj['name'] = metaValue
                    facilitators_names.insert(int(facilitatorNumber)-1,metaValue)
                elif metaType == 'twitter':
                    facilitatorObj['twitter'] = metaValue
                elif metaType == 'affiliated':
                    facilitatorObj['affiliated org'] = metaValue

                facilitators[facilitatorNumber] = facilitatorObj

        _transformed_item['facilitators'] = facilitators
        _transformed_item['facilitators_names'] = facilitators_names

        # transform column name "CUSTOM_CATEGORY_LABEL" into JSON key `category`
        _transformed_item['category'] = _transformed_item.pop(CUSTOM_CATEGORY_LABEL, '')

        # remove invalid tag labels that were used for GitHub workflow and transform column name "CUSTOM_TAGS_LABEL" into JSON key `tags`
        tag_skip_keywords = ['accepted','consideration','stipend','sample']
        tag_list = _transformed_item[CUSTOM_TAGS_LABEL].split(',')
        tag_list = [
            name for name in tag_list if not set(tag_skip_keywords).intersection(set(name.lower().split()))
        ]
        _transformed_item['tags'] = ','.join(tag_list)
        _transformed_item.pop(CUSTOM_TAGS_LABEL, '')

        # create `timeblock` key based on `timeblock`
        time_data = _transformed_item.pop('timeblock', '')
        timeblock = slugify_timeblock(time_data)
        _transformed_item['timeblock'] = timeblock

        # infer session day
        if 'Monday' in time_data:
            _transformed_item['day'] = 'Monday'
        if 'Tuesday' in time_data:
            _transformed_item['day'] = 'Tuesday'
        if 'Wednesday' in time_data:
            _transformed_item['day'] = 'Wednesday'
        if 'Thursday' in time_data:
            _transformed_item['day'] = 'Thursday'
        if 'Friday' in time_data:
            _transformed_item['day'] = 'Friday'
        if 'Saturday' in time_data:
            _transformed_item['day'] = 'Saturday'
        if 'Sunday' in time_data:
            _transformed_item['day'] = 'Sunday'

        # start time
        if len(time_data) > 1:
            start_time = time_data.split('(')
            start_time = start_time[len(start_time)-1].strip(')')[-5:] # return the last 5 character
            duration = _transformed_item.pop('duration', '') or 0

            try:
                # attempt to coerce to 12-hour format
                d = datetime.strptime(start_time, "%H:%M")
                start_time = d.strftime("%I:%M").lstrip('0') + d.strftime('%p').lower()
                duration = int(duration)
                end_time = d + timedelta(minutes=duration)
                end_time = end_time.strftime("%I:%M").lstrip('0') + end_time.strftime('%p').lower()

                if start_time[0] == '0':
                    # strip leading 0
                    start_time =  start_time[1:]

            except:
                start_time = ''
                end_time = ''
                pass
            _transformed_item['start'] = start_time
            _transformed_item['end'] = end_time
        
         
        # prepend `location` with the word 'Floor'
        # if _transformed_item['location'] and not _transformed_item['location'].startswith('Floor'):
        #     _transformed_item['location'] = 'Floor {0}'.format(_transformed_item['location'])

        # mark as "programmatic" session if session's category is 'Programmatic Pieces'
        # removes category meta since 'Programmatic Pieces' isn't a real [category] (e.g, MozFest Space)
        _transformed_item['programmatic'] = True if _transformed_item['category'] == 'Programmatic Pieces' else False
        if _transformed_item['programmatic']:
            _transformed_item['category'] = None
                
        # if we've triggered the skip flag anywhere, drop this record
        if skip:
            _transformed_item = None
            
        return _transformed_item
    
    # empty list to hold any items we need to duplicate
    cloned_data = []
    # pass initial data through the transformer
    transformed_data = filter(None, [_transform_response_item(item) for item in data])
    # and add in any items we had to duplicate
    transformed_data.extend(
        filter(None, [_transform_response_item(item) for item in cloned_data])
    )

    return transformed_data

def make_json(data, store_locally=False, filename=GITHUB_CONFIG['TARGET_FILE']):
    '''
    Turns data into nice JSON, and optionally stores to a local file.
    '''
    json_out = json.dumps(data, sort_keys=True, indent=4, ensure_ascii=False)
    
    if store_locally:
        with io.open(filename, 'w', encoding='utf8') as outfile:
            outfile.write(unicode(json_out))

    return json_out.encode('utf-8')

def commit_json(data, target_config=GITHUB_CONFIG, commit=COMMIT_JSON_TO_GITHUB):
    '''
    Uses token to log into GitHub as `ryanpitts`, then gets the appropriate
    repo based on owner/name defined in GITHUB_CONFIG.
    
    Creates sessions data file if it does not exist in the repo, otherwise
    updates existing data file.
    
    If `COMMIT_JSON_TO_GITHUB` is False, this will operate in "dry run" mode,
    authenticating against GitHub but not changing any files.
    '''
    
    # authenticate with GitHub
    gh = github3.login(token=target_config['TOKEN'])
    
    # get the right repo
    repo = gh.repository(target_config['REPO_OWNER'], target_config['REPO_NAME'])
    repo_location = os.environ['REPO_OWNER'] + "/" + os.environ['REPO_NAME']
    file_path = target_config['TARGET_DIR'] + target_config['TARGET_FILE']

    for branch in target_config['TARGET_BRANCHES']:
        # check to see whether data file exists
        contents = repo.contents(
            path=file_path,
            ref=branch
        )

        confirm_commit = False

        if commit:
            if PROMPT_BEFORE_COMMIT_TO_GITHUB:
                confirm_commit = raw_input("[Please Confirm] Commit " + file_path + " to repo " + repo_location + "? (Y/N): ")
            else:
                confirm_commit = "Y"

        if confirm_commit == "Y":
            if not contents:
                # create file that doesn't exist
                repo.create_file(
                    path=file_path,
                    message='adding session data',
                    content=data,
                    branch=branch
                )
                logger.info('Created new data file ' + file_path + ' in repo ' + repo_location)
            else:
                # if data has changed, update existing file
                if data.decode('utf-8') == contents.decoded.decode('utf-8'):
                    logger.info('Data has not changed, no commit created')
                else:
                    repo.update_file(
                        path=file_path,
                        message='updating schedule data',
                        content=data,
                        sha=contents.sha,
                        branch=branch
                    )
                    logger.info('Data updated! Updated ' + file_path +' has been committed to repo ' + repo_location)
                

def update_schedule():
    data = fetch_data(multiple_sheets=FETCH_MULTIPLE_WORKSHEETS, sessions_worksheets_to_fetch=SESSIONS_WORKSHEETS_TO_FETCH)
    print 'Fetched the data ...'

    data = {
        'timeblocks': transform_timeblock_data(data['timeblocks']),
        'sessions': transform_session_data(data['sessions'])
    }
    #print 'Prepped the data ...'

    session_json = make_json(data, store_locally=MAKE_LOCAL_JSON)
    #print 'Made the local json!'

    commit_json(session_json)
    #print 'SENT THE DATA TO GITHUB!'


'''
Set up logging.
'''
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        'verbose': {
            'format': '%(levelname)s %(asctime)s %(message)s'
        },
        'simple': {
            'format': '%(levelname)s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'log.txt',
            'formatter': 'verbose'
        },
        'console':{
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
    },
    'loggers': {
        'schedule_loader': {
            'handlers':['file','console'],
            'propagate': False,
            'level':'DEBUG',
        }
    }
}
dictConfig(LOGGING)
logger = logging.getLogger('schedule_loader')


if __name__ == "__main__":
    try:
        update_schedule()
    except Exception, e:
        sys.stderr.write('\n')
        traceback.print_exc(file=sys.stderr)
        sys.stderr.write('\n')
        sys.exit(1)
