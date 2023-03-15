import time
import tkinter as tk
import tkinter.ttk as ttk
import datetime
import tkinter.messagebox
import BaseFunctions as b
import threading
import os
import re
import subprocess

appDataRoamingPath = os.getenv('APPDATA')
clientTimerFolderPath = appDataRoamingPath + "\\Client Timer 1"
configPath = clientTimerFolderPath + "\\config.txt"
recentSavePath = clientTimerFolderPath + "\\recent_save.txt"
backupPath = clientTimerFolderPath + "\\Backups"



if(not os.path.exists(clientTimerFolderPath)):
    os.mkdir(clientTimerFolderPath)
if(not os.path.exists(backupPath)):
    os.mkdir(backupPath)
if(not os.path.exists(configPath)):
    configString = '''#####################
#######CLIENTS#######
#####################

# Here is a list of all clients that will be listed in the timer app. To add
# an additional client, simply add a new string ('clientName') preceded with
# a comma.
> clientList = []

# This option controls the main color theme of the program.
> programColorTheme = Classic Light


# This option controls the general size of font and buttons
> programSize = Regular
'''
    configFile = open(configPath,"w")
    configFile.write(configString)
    configFile.close()
if(not b.testForConfigValue(parameterName="clientList",configFilePath=configPath)):
    b.setConfigValue(parameterName="clientList",parameterValue=[],configFilePath=configPath)
if(not b.testForConfigValue(parameterName="programColorTheme",configFilePath=configPath)):
    b.setConfigValue(parameterName="programColorTheme",parameterValue="Classic Light",configFilePath=configPath)
if(not b.testForConfigValue(parameterName="programSize",configFilePath=configPath)):
    b.setConfigValue(parameterName="programSize",parameterValue="Regular",configFilePath=configPath)


ZEROTIME = 946706400.0

THEME_DICT = {"Classic Light" : {"NORMAL_TEXT_COLOR" : "#010108", "BUTTON_TEXT_COLOR" : "#010108", "WINDOW_BG_COLOR" : "#EDEDED", "BUTTON_BG_OFF_COLOR" : "#BDBDBD", "BUTTON_BG_ON_COLOR" : "#8F8F8F", "BUTTON_BORDER_SIZE" : "1"},
              "Classic Dark" : {"NORMAL_TEXT_COLOR" : "#DBDBDB", "BUTTON_TEXT_COLOR" : "#DBDBDB", "WINDOW_BG_COLOR" : "#1C1C1C", "BUTTON_BG_OFF_COLOR" : "#4F4F4F", "BUTTON_BG_ON_COLOR" : "#BDBDBD","BUTTON_BORDER_SIZE" : "0"},
              "Single Pane of Glass" : {"NORMAL_TEXT_COLOR" : "#EAFFFF", "BUTTON_TEXT_COLOR" : "#4C4C4C", "WINDOW_BG_COLOR" : "#2574DB", "BUTTON_BG_OFF_COLOR" : "#F3F5F7", "BUTTON_BG_ON_COLOR" : "#184885","BUTTON_BORDER_SIZE" : "0"},
              "Twilight Carrier" : {"NORMAL_TEXT_COLOR" : "#FFD700", "BUTTON_TEXT_COLOR" : "#000000", "WINDOW_BG_COLOR" : "#16527D", "BUTTON_BG_OFF_COLOR" : "#E9E9ED", "BUTTON_BG_ON_COLOR" : "#B5B5B9","BUTTON_BORDER_SIZE" : "0"},
              "Black Herizons" : {"NORMAL_TEXT_COLOR" : "#EE0000", "BUTTON_TEXT_COLOR" : "#010108", "WINDOW_BG_COLOR" : "#000000", "BUTTON_BG_OFF_COLOR" : "#FFFFFF", "BUTTON_BG_ON_COLOR" : "#FF8080","BUTTON_BORDER_SIZE" : "1"},
              "Pretty In Pink-Mobile" : {"NORMAL_TEXT_COLOR" : "#E20074", "BUTTON_TEXT_COLOR" : "#262626", "WINDOW_BG_COLOR" : "#FFFFFF", "BUTTON_BG_OFF_COLOR" : "#E8E8E8", "BUTTON_BG_ON_COLOR" : "#9F9F9F","BUTTON_BORDER_SIZE" : "1"},
              "Nothing-Else-In-Stock Green" : {"NORMAL_TEXT_COLOR" : "#5DF5B8", "BUTTON_TEXT_COLOR" : "#5DF5B8", "WINDOW_BG_COLOR" : "#667765", "BUTTON_BG_OFF_COLOR" : "#394E3F", "BUTTON_BG_ON_COLOR" : "#243128","BUTTON_BORDER_SIZE" : "0"},
              "50 Shades Of Teams Popups" : {"NORMAL_TEXT_COLOR" : "#FFFDFF", "BUTTON_TEXT_COLOR" : "#FFFDFF", "WINDOW_BG_COLOR" : "#7C7692", "BUTTON_BG_OFF_COLOR" : "#6363A9", "BUTTON_BG_ON_COLOR" : "#C4314D","BUTTON_BORDER_SIZE" : "0"},
              "Unavailable: Orange Getup" : {"NORMAL_TEXT_COLOR" : "#FBFFFF", "BUTTON_TEXT_COLOR" : "#A7E3F2", "WINDOW_BG_COLOR" : "#E85D46", "BUTTON_BG_OFF_COLOR" : "#007CBE", "BUTTON_BG_ON_COLOR" : "#13628C","BUTTON_BORDER_SIZE" : "1"},
              }
THEME = b.readConfigValue("programColorTheme",configFilePath=configPath)
NORMAL_TEXT_COLOR = THEME_DICT[THEME]["NORMAL_TEXT_COLOR"]
BUTTON_TEXT_COLOR = THEME_DICT[THEME]["BUTTON_TEXT_COLOR"]
WINDOW_BG_COLOR = THEME_DICT[THEME]["WINDOW_BG_COLOR"]
BUTTON_BG_ON_COLOR = THEME_DICT[THEME]["BUTTON_BG_ON_COLOR"]
BUTTON_BG_OFF_COLOR = THEME_DICT[THEME]["BUTTON_BG_OFF_COLOR"]
BUTTON_BORDER_SIZE = THEME_DICT[THEME]["BUTTON_BORDER_SIZE"]

SIZE_DICT = {"Microscopic" : {"CLIENT_LABEL_SIZE" : 9, "TIME_TEXT_SIZE" : 7, "ACTION_TEXT_SIZE" : 6, "CONFIG_BUTTON_PADY" : 0, "CLIENT_FRAME_PADX" : 0, "CLIENT_FRAME_PADY" : 0},
             "Tiny" : {"CLIENT_LABEL_SIZE" : 10, "TIME_TEXT_SIZE" : 8, "ACTION_TEXT_SIZE" : 7, "CONFIG_BUTTON_PADY" : 1, "CLIENT_FRAME_PADX" : 0, "CLIENT_FRAME_PADY" : 1},
             "Compact" : {"CLIENT_LABEL_SIZE" : 12, "TIME_TEXT_SIZE" : 10, "ACTION_TEXT_SIZE" : 8, "CONFIG_BUTTON_PADY" : 2, "CLIENT_FRAME_PADX" : 3, "CLIENT_FRAME_PADY" : 4},
             "Regular" : {"CLIENT_LABEL_SIZE" : 14, "TIME_TEXT_SIZE" : 12, "ACTION_TEXT_SIZE" : 9, "CONFIG_BUTTON_PADY" : 5, "CLIENT_FRAME_PADX" : 5, "CLIENT_FRAME_PADY" : 7},
             "Bulky" : {"CLIENT_LABEL_SIZE" : 16, "TIME_TEXT_SIZE" : 14, "ACTION_TEXT_SIZE" : 11, "CONFIG_BUTTON_PADY" : 5, "CLIENT_FRAME_PADX" : 5, "CLIENT_FRAME_PADY" : 8},
             "Colossal" : {"CLIENT_LABEL_SIZE" : 18, "TIME_TEXT_SIZE" : 16, "ACTION_TEXT_SIZE" : 13, "CONFIG_BUTTON_PADY" : 7, "CLIENT_FRAME_PADX" : 7, "CLIENT_FRAME_PADY" : 10},
             "Gargantuan" : {"CLIENT_LABEL_SIZE" : 22, "TIME_TEXT_SIZE" : 20, "ACTION_TEXT_SIZE" : 18, "CONFIG_BUTTON_PADY" : 9, "CLIENT_FRAME_PADX" : 9, "CLIENT_FRAME_PADY" : 12}
             }
SIZE = b.readConfigValue("programSize",configFilePath=configPath)
CLIENT_LABEL_SIZE = SIZE_DICT[SIZE]["CLIENT_LABEL_SIZE"]
TIME_TEXT_SIZE = SIZE_DICT[SIZE]["TIME_TEXT_SIZE"]
ACTION_TEXT_SIZE = SIZE_DICT[SIZE]["ACTION_TEXT_SIZE"]
CONFIG_BUTTON_PADY = SIZE_DICT[SIZE]["CONFIG_BUTTON_PADY"]
CLIENT_FRAME_PADX = SIZE_DICT[SIZE]["CLIENT_FRAME_PADX"]
CLIENT_FRAME_PADY = SIZE_DICT[SIZE]["CLIENT_FRAME_PADY"]

class ClientTracker:

    # Init method opens tkinter.root, builds initial window.
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Client Timer")
        if(os.path.exists("icon.ico")):
            self.root.wm_iconbitmap("icon.ico")
        self.root.configure(bg=WINDOW_BG_COLOR)
        self.root.wm_attributes("-topmost", 1)
        self.root.protocol("WM_DELETE_WINDOW", self.close_program)

        self.loadedClients = {}
        self.runningClients = []
        self.clientList = []

        # Create a frame for the client list
        self.client_frame = tk.Frame(self.root,background=WINDOW_BG_COLOR)
        self.client_frame.grid(row=0,column=0,columnspan=2,pady=CLIENT_FRAME_PADY,padx=CLIENT_FRAME_PADX)

        self.staticCounter = 0


        self.clientAddingFrame = tk.Frame(self.root,background=WINDOW_BG_COLOR)
        self.clientAddingFrame.grid(row=1, column=0, pady=CONFIG_BUTTON_PADY, padx=CLIENT_FRAME_PADX,sticky='ew')
        self.client_input = tk.Entry(self.clientAddingFrame,font=("Helvetica",TIME_TEXT_SIZE),bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,bd=BUTTON_BORDER_SIZE)
        self.add_client_button = tk.Button(self.clientAddingFrame, text="Add Client",font=("Helvetica",ACTION_TEXT_SIZE),command=lambda: self.add_client(re.sub(r'[^a-zA-Z0-9\s\'.]+', '', self.client_input.get()).rstrip(" ")), bg=BUTTON_BG_OFF_COLOR, fg=BUTTON_TEXT_COLOR, activebackground=BUTTON_BG_ON_COLOR, activeforeground=BUTTON_TEXT_COLOR, bd=BUTTON_BORDER_SIZE)
        self.configuration_button = tk.Button(self.root, text="Configuration",font=("Helvetica",ACTION_TEXT_SIZE), command=lambda: self.openConfigMenu(),fg=BUTTON_TEXT_COLOR,bg=BUTTON_BG_OFF_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
        self.add_client_button.grid(row=0, column=0,sticky='w')
        self.client_input.grid(row=0,column=1,padx=2,sticky='w')
        self.configuration_button.grid(row=1,column=1,sticky='e',padx=CLIENT_FRAME_PADX,pady=CONFIG_BUTTON_PADY)

        self.genClientList()

        if(self.check_for_save()):
            self.restore_save()
    # Close program method attempts to save times as a recent saves, so you can come back
    # later.
    def close_program(self):
        try:
            self.save_times()
            self.root.destroy()
        except:
            tk.messagebox.showerror('Program Crash', 'Error: The program crashed while trying to save data.')
            self.root.destroy()

    # Functions to add and remove a client to the list. These changes are permanent, and will
    # persist through multiple executions of the program.
    def add_client(self,clientName):
        if(clientName in self.loadedClients.keys()):
            tk.messagebox.showerror('Duplicate Error', 'Error: A client with the same name already exists!')
            return False
        else:
            b.setConfigValue(parameterName="clientList",parameterValue=b.readConfigValue(parameterName="clientList",configFilePath=configPath) + [clientName],configFilePath=configPath)
            self.genClientList()
            return True
    def remove_client(self,clientName):
        newArray = b.readConfigValue(parameterName="clientList",configFilePath=configPath)
        newArray.remove(clientName)
        b.setConfigValue(parameterName="clientList", parameterValue=newArray, configFilePath=configPath)
        self.remove_timer(clientName)
        self.loadedClients.pop(clientName)
        self.genClientList()
        if(clientName in self.runningClients):
            self.runningClients.remove(clientName)
        return True
    # This method resets all times of all clients, after prompting the user with a confirmation box.
    def reset_times(self):
        if(tkinter.messagebox.askyesno("Confirm","Are you sure you would like to reset all times?")):
            self.stop_timer()
            for loadedClient in self.loadedClients.keys():
                self.loadedClients[loadedClient]["saved_time"] = ZEROTIME
            self.genClientList()
    # This method populates the app with all clients in self.clientList. It also
    # can reload the clientList from the config file.
    def genClientList(self,reloadFile=True):
        for widget in self.client_frame.grid_slaves():
            widget.grid_forget()

        if(reloadFile):
            self.clientList = b.readConfigValue(parameterName="clientList",configFilePath=configPath)
            if(self.clientList == False):
                self.clientList = []


        if(len(self.clientList) == 0):
            blank_label = tk.Label(self.client_frame, text="No clients. Add one to begin!", font=('Helvetica', CLIENT_LABEL_SIZE),fg=NORMAL_TEXT_COLOR,bg=WINDOW_BG_COLOR)
            blank_label.grid(row=0,column=0)
        else:
            counter = 0

            maximumLength = 0
            for client in self.clientList:
                if(len(client) > maximumLength):
                    maximumLength = len(client)
            # Create a label for each client
            for client in self.clientList:
                client = client.strip()
                client_label = ttk.Label(self.client_frame, text=client + ":", font=('Helvetica', CLIENT_LABEL_SIZE),width=maximumLength+1,anchor="center",background=WINDOW_BG_COLOR,foreground=NORMAL_TEXT_COLOR)
                client_label.grid(row=counter,column=0)

                # Create a start button for each client
                start_button = tk.Button(self.client_frame, text="Start",font=('Helvetica', TIME_TEXT_SIZE),bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
                start_button.grid(row=counter,column=1)

                start_button.bind("<Button-1>",lambda event, _client = client: self.start_timer(_client))
                start_button.bind("<Shift-Button-1>", lambda event, _client = client: self.add_timer(_client))

                # Create a stop button for each client
                stop_button = tk.Button(self.client_frame, text="Stop",font=('Helvetica', TIME_TEXT_SIZE),bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
                stop_button.grid(row=counter,column=2)

                stop_button.bind("<Button-1>",lambda event: self.stop_timer())
                stop_button.bind("<Shift-Button-1>",lambda event,_client = client: self.remove_timer(_client))



                removeTimeButton = tk.Button(self.client_frame, text="-5", font=("Helvetica", ACTION_TEXT_SIZE),bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
                removeTimeButton.grid(row=counter, column=4)
                addTimeButton = tk.Button(self.client_frame, text="+5", font=("Helvetica", ACTION_TEXT_SIZE),bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
                addTimeButton.grid(row=counter, column=5)

                addTimeButton.bind("<Button-1>", lambda event, _client=client: self.adjust_timer(_client,300))
                addTimeButton.bind("<Shift-Button-1>", lambda event, _client=client: self.adjust_timer(_client,60))

                removeTimeButton.bind("<Button-1>", lambda event, _client=client: self.adjust_timer(_client, -300))
                removeTimeButton.bind("<Shift-Button-1>", lambda event, _client=client: self.adjust_timer(_client, -60))

                removeClientButton = tk.Button(self.client_frame,text="X",font=("Helvetica",ACTION_TEXT_SIZE),command=lambda _client=client: self.remove_client(_client),bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
                removeClientButton.grid(row=counter,column=6)

                # Create a label to display the running time for each client
                time_label = ttk.Label(self.client_frame,font=('Helvetica', TIME_TEXT_SIZE),width=8,anchor="center",background=WINDOW_BG_COLOR,foreground=NORMAL_TEXT_COLOR)
                time_label.grid(row=counter,column=3)

                if(client in self.loadedClients):
                    if(self.loadedClients[client]["running"]):
                        time_label.configure(font=('Helvetica', TIME_TEXT_SIZE,"bold"))
                        client_label.configure(font=('Helvetica', CLIENT_LABEL_SIZE,"bold"))
                        time_label.configure(text=datetime.datetime.strftime(datetime.datetime.fromtimestamp(self.loadedClients[client]["saved_time"] + (time.perf_counter() - self.loadedClients[client]["start_time"])), '%H:%M:%S'))
                    else:
                        time_label.configure(text=datetime.datetime.strftime(datetime.datetime.fromtimestamp(self.loadedClients[client]["saved_time"]), '%H:%M:%S'))
                    self.loadedClients[client]["time_label"] = time_label
                    self.loadedClients[client]["client_label"] = client_label
                    counter += 1
                else:
                    time_label.configure(text="00:00:00")
                    # Add the time label to the dictionary of clients
                    self.loadedClients[client] = {"client_label" : client_label, "time_label" : time_label, "running": False, "saved_time" : ZEROTIME, "start_time" : None}
                    counter += 1

    # This method opens the config menu.
    def openConfigMenu(self):
        self.stop_timer()
        configMenu = tk.Toplevel(self.root)
        configMenu.title("Configuration")
        #configMenu.geometry("500x185")

        configMenu.configure(bg=WINDOW_BG_COLOR)
        configMenu.wm_attributes("-topmost", 1)
        configMenu.grab_set()

        liveExampleFrame = tk.Frame(configMenu,bg=WINDOW_BG_COLOR,bd=2,relief=tk.SOLID)
        liveExampleFrame.grid(column=0,row=2,columnspan=3,pady=5,padx=10,sticky='w')
        exampleClientLabel = ttk.Label(liveExampleFrame, text="Acme Corp:", font=('Helvetica', CLIENT_LABEL_SIZE), width=7, anchor="center", background=WINDOW_BG_COLOR, foreground=NORMAL_TEXT_COLOR)
        exampleClientLabel.grid(row=0, column=0,pady=5)
        exampleStartButton = tk.Button(liveExampleFrame, text="Start", font=('Helvetica', TIME_TEXT_SIZE), bg=BUTTON_BG_OFF_COLOR, fg=BUTTON_TEXT_COLOR, activeforeground=BUTTON_TEXT_COLOR, activebackground=BUTTON_BG_ON_COLOR, bd=BUTTON_BORDER_SIZE)
        exampleStartButton.grid(row=0, column=1,pady=5)
        exampleStopButton = tk.Button(liveExampleFrame, text="Stop", font=('Helvetica', TIME_TEXT_SIZE), bg=BUTTON_BG_OFF_COLOR, fg=BUTTON_TEXT_COLOR, activeforeground=BUTTON_TEXT_COLOR, activebackground=BUTTON_BG_ON_COLOR, bd=BUTTON_BORDER_SIZE)
        exampleStopButton.grid(row=0, column=2,pady=5)
        exampleRemoveTimeButton = tk.Button(liveExampleFrame, text="-5", font=("Helvetica", ACTION_TEXT_SIZE), bg=BUTTON_BG_OFF_COLOR, fg=BUTTON_TEXT_COLOR, activeforeground=BUTTON_TEXT_COLOR, activebackground=BUTTON_BG_ON_COLOR, bd=BUTTON_BORDER_SIZE)
        exampleRemoveTimeButton.grid(row=0, column=4,pady=5)
        exampleAddTimeButton = tk.Button(liveExampleFrame, text="+5", font=("Helvetica", ACTION_TEXT_SIZE), bg=BUTTON_BG_OFF_COLOR, fg=BUTTON_TEXT_COLOR, activeforeground=BUTTON_TEXT_COLOR, activebackground=BUTTON_BG_ON_COLOR, bd=BUTTON_BORDER_SIZE)
        exampleAddTimeButton.grid(row=0, column=5,pady=5)
        exampleRemoveClientButton = tk.Button(liveExampleFrame, text="X", font=("Helvetica", ACTION_TEXT_SIZE), bg=BUTTON_BG_OFF_COLOR, fg=BUTTON_TEXT_COLOR, activeforeground=BUTTON_TEXT_COLOR, activebackground=BUTTON_BG_ON_COLOR, bd=BUTTON_BORDER_SIZE)
        exampleRemoveClientButton.grid(row=0, column=6,pady=5)
        exampleTimeLabel = ttk.Label(liveExampleFrame,text="00:05:21", font=('Helvetica', TIME_TEXT_SIZE), width=8, anchor="center", background=WINDOW_BG_COLOR, foreground=NORMAL_TEXT_COLOR)
        exampleTimeLabel.grid(row=0, column=3,pady=5)
        extraExampleSpace = ttk.Label(liveExampleFrame,text="",width=1,background=WINDOW_BG_COLOR)
        extraExampleSpace.grid(row=0,column=7)

        selectedTheme = tk.StringVar()
        selectedTheme.set(THEME)
        selectedSize = tk.StringVar()
        selectedSize.set(SIZE)
        # This method updates the example display with all values currently in
        # the two dropdown menus.
        def updateExample(event):
            testTheme = selectedTheme.get()
            testSize = selectedSize.get()


            exampleNormalTextColor = THEME_DICT[testTheme]["NORMAL_TEXT_COLOR"]
            exampleButtonTextColor = THEME_DICT[testTheme]["BUTTON_TEXT_COLOR"]
            exampleWindowBGColor = THEME_DICT[testTheme]["WINDOW_BG_COLOR"]
            exampleButtonBGOnColor = THEME_DICT[testTheme]["BUTTON_BG_ON_COLOR"]
            exampleButtonBGOffColor = THEME_DICT[testTheme]["BUTTON_BG_OFF_COLOR"]
            exampleButtonBorderSize = THEME_DICT[testTheme]["BUTTON_BORDER_SIZE"]

            exampleClientLabelSize = SIZE_DICT[testSize]["CLIENT_LABEL_SIZE"]
            exampleTimeTextSize = SIZE_DICT[testSize]["TIME_TEXT_SIZE"]
            exampleActionTextSize = SIZE_DICT[testSize]["ACTION_TEXT_SIZE"]

            liveExampleFrame.configure(background=exampleWindowBGColor)
            exampleClientLabel.configure(font=('Helvetica', exampleClientLabelSize), width=7, anchor="center", background=exampleWindowBGColor, foreground=exampleNormalTextColor)
            exampleStartButton.configure(font=('Helvetica', exampleTimeTextSize), bg=exampleButtonBGOffColor, fg=exampleButtonTextColor, activeforeground=exampleButtonTextColor, activebackground=exampleButtonBGOnColor, bd=exampleButtonBorderSize)
            exampleStopButton.configure(font=('Helvetica', exampleTimeTextSize), bg=exampleButtonBGOffColor, fg=exampleButtonTextColor, activeforeground=exampleButtonTextColor, activebackground=exampleButtonBGOnColor, bd=exampleButtonBorderSize)
            exampleRemoveTimeButton.configure(font=("Helvetica", exampleActionTextSize), bg=exampleButtonBGOffColor, fg=exampleButtonTextColor, activeforeground=exampleButtonTextColor, activebackground=exampleButtonBGOnColor, bd=exampleButtonBorderSize)
            exampleAddTimeButton.configure(font=("Helvetica", exampleActionTextSize), bg=exampleButtonBGOffColor, fg=exampleButtonTextColor, activeforeground=exampleButtonTextColor, activebackground=exampleButtonBGOnColor, bd=exampleButtonBorderSize)
            exampleRemoveClientButton.configure(font=("Helvetica", exampleActionTextSize), bg=exampleButtonBGOffColor, fg=exampleButtonTextColor, activeforeground=exampleButtonTextColor, activebackground=exampleButtonBGOnColor, bd=exampleButtonBorderSize)
            exampleTimeLabel.configure(font=('Helvetica', exampleTimeTextSize), width=8, anchor="center", background=exampleWindowBGColor, foreground=exampleNormalTextColor)
            extraExampleSpace.configure(background=exampleWindowBGColor)


        themeLabel = tk.Label(configMenu,text="Program Theme:",bg=WINDOW_BG_COLOR,fg=NORMAL_TEXT_COLOR,font=("Helvetica", 14, "bold"))
        themeOptions = tk.OptionMenu(configMenu,selectedTheme,*list(THEME_DICT.keys()),command=updateExample)
        themeOptions.configure(bg=BUTTON_BG_OFF_COLOR,activebackground=BUTTON_BG_ON_COLOR,fg=BUTTON_TEXT_COLOR,bd=BUTTON_BORDER_SIZE,highlightthickness=0,width=23,anchor="w")
        themeLabel.grid(column=0,row=0,padx=5,columnspan=2)
        themeOptions.grid(column=2,row=0,padx=5,columnspan=2)

        sizeLabel = tk.Label(configMenu,text="Program Size:",bg=WINDOW_BG_COLOR,fg=NORMAL_TEXT_COLOR,font=("Helvetica", 14, "bold"))
        sizeOptions = tk.OptionMenu(configMenu,selectedSize,*list(SIZE_DICT.keys()),command=updateExample)
        sizeOptions.configure(bg=BUTTON_BG_OFF_COLOR,activebackground=BUTTON_BG_ON_COLOR,fg=BUTTON_TEXT_COLOR,bd=BUTTON_BORDER_SIZE,highlightthickness=0,width=23,anchor="w")
        sizeLabel.grid(column=0,row=1,padx=5,columnspan=2)
        sizeOptions.grid(column=2,row=1,padx=5,columnspan=2)


        # Simply applies all changed settings with the theme and program size.
        def applySettings():
            needsRestart = False
            selectedThemeString = selectedTheme.get()
            if(selectedThemeString != THEME):
                b.setConfigValue("programColorTheme",selectedThemeString,configFilePath=configPath)
                needsRestart = True

            selectedSizeString = selectedSize.get()
            if(selectedSizeString != SIZE):
                b.setConfigValue("programSize",selectedSizeString,configFilePath=configPath)
                needsRestart = True

            configMenu.destroy()
            if(needsRestart):
                tk.messagebox.showinfo(title="Restart Required",message="Please restart the app to apply your changes.")
        applyButton = tk.Button(configMenu,text="Apply Style",command=applySettings,bg=BUTTON_BG_OFF_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,activebackground=BUTTON_BG_ON_COLOR,bd=BUTTON_BORDER_SIZE)
        applyButton.grid(column=3,row=2,padx=5)


        divider = tk.ttk.Separator(configMenu,orient='horizontal')
        divider.grid(column=0,row=3,columnspan=6,sticky='ew',pady=5)

        resetTimesButton = tk.Button(configMenu,text="Reset All Times",command=self.reset_times,bg=BUTTON_BG_OFF_COLOR,activebackground=BUTTON_BG_ON_COLOR,activeforeground=BUTTON_TEXT_COLOR,fg=BUTTON_TEXT_COLOR,bd=BUTTON_BORDER_SIZE)
        resetTimesButton.grid(column=0,row=4,pady=10,sticky='w',padx=5)

        print(clientTimerFolderPath)
        openSaveFolderButton = tk.Button(configMenu,text="Open Save Folder",command=lambda: subprocess.Popen(f'explorer "{clientTimerFolderPath}"'),bg=BUTTON_BG_OFF_COLOR,activebackground=BUTTON_BG_ON_COLOR,fg=BUTTON_TEXT_COLOR,activeforeground=BUTTON_TEXT_COLOR,bd=BUTTON_BORDER_SIZE)
        openSaveFolderButton.grid(column=3,row=4,pady=10,padx=5,sticky='e')

    # Simple start and stop methods for each timer. Start requires the clientName,
    # but stop works off of the self.runningClient variable.
    def start_timer(self,clientName):
        rightNow = time.perf_counter()
        self.stop_timer()
        self.runningClients = [clientName]

        # Set the running flag to True and store the start time
        self.loadedClients[clientName]["running"] = True
        self.loadedClients[clientName]["start_time"] = rightNow
        self.loadedClients[clientName]["time_label"].configure(font=('Helvetica', TIME_TEXT_SIZE, "bold"))
        self.loadedClients[clientName]["client_label"].configure(font=('Helvetica', CLIENT_LABEL_SIZE, "bold"))

        self.update_time_label()
    def add_timer(self,clientName):
        if (clientName not in self.runningClients):
            rightNow = time.perf_counter()
            self.runningClients += [clientName]

            # Set the running flag to True and store the start time
            self.loadedClients[clientName]["running"] = True
            self.loadedClients[clientName]["start_time"] = rightNow
            self.loadedClients[clientName]["time_label"].configure(font=('Helvetica', TIME_TEXT_SIZE, "bold"))
            self.loadedClients[clientName]["client_label"].configure(font=('Helvetica', CLIENT_LABEL_SIZE, "bold"))
    def stop_timer(self):
        if(len(self.runningClients) > 0):
            for runningClient in self.runningClients:
                self.loadedClients[runningClient]["saved_time"] += (time.perf_counter() - self.loadedClients[runningClient]["start_time"])
                self.loadedClients[runningClient]["running"] = False
                self.loadedClients[runningClient]["start_time"] = None
                self.loadedClients[runningClient]["time_label"].configure(font=('Helvetica', TIME_TEXT_SIZE))
                self.loadedClients[runningClient]["client_label"].configure(font=('Helvetica', CLIENT_LABEL_SIZE))


        self.runningClients = []
    def remove_timer(self,clientName):
        if (clientName in self.runningClients):
            self.runningClients.remove(clientName)
            self.loadedClients[clientName]["saved_time"] += (time.perf_counter() - self.loadedClients[clientName]["start_time"])

            # Set the running flag to True and store the start time
            self.loadedClients[clientName]["running"] = False
            self.loadedClients[clientName]["start_time"] = None
            self.loadedClients[clientName]["time_label"].configure(font=('Helvetica', TIME_TEXT_SIZE))
            self.loadedClients[clientName]["client_label"].configure(font=('Helvetica', CLIENT_LABEL_SIZE))
    # This method can be used to add or remove a set amount of time from a timer.
    def adjust_timer(self,clientName,timeAdjustment):
        rightNow = time.perf_counter()
        thisClientStartTime = self.loadedClients[clientName]["start_time"]
        if(thisClientStartTime is None):
            self.loadedClients[clientName]["saved_time"] += timeAdjustment
            if(self.loadedClients[clientName]["running"]):
                self.loadedClients[clientName]["start_time"] = rightNow
        else:
            self.loadedClients[clientName]["saved_time"] += ((rightNow - self.loadedClients[clientName]["start_time"]) + timeAdjustment)
            if (self.loadedClients[clientName]["running"]):
                self.loadedClients[clientName]["start_time"] = rightNow
        self.update_time_label(updateAll=True)

    # This method restores existing time values from an existing save file. check_for_save
    # checks to see if a file exists from TODAY with saved time values, and if it does, it prompts
    # the user to ask if they want to restore from that backup or not.
    def restore_save(self):
        thisFile = open(recentSavePath,"r")
        counter = -1
        for line in thisFile:
            counter += 1
            if(counter < 2):
                continue
            if(line.strip() == ""):
                continue
            lineArray = line.split("|")
            savedClient = lineArray[0].strip()
            savedTime = lineArray[1].strip()
            if (savedClient == "" or savedTime == ""):
                continue

            if(savedClient not in self.loadedClients):
                self.add_client(savedClient)

            try:
                timeObject = datetime.datetime.strptime(savedTime,"%H:%M:%S")
                timeObject = timeObject.replace(year=2000)
                self.loadedClients[savedClient]["saved_time"] = timeObject.timestamp()
            except ValueError:
                continue

        self.update_time_label(updateAll=True)
        thisFile.close()
    def check_for_save(self):
        if(os.path.exists(recentSavePath)):
            thisFile = open(recentSavePath,"r")
            today = datetime.datetime.today()
            try:
                fileStamp = datetime.datetime.strptime(thisFile.readlines()[0].strip(),"%m/%d/%y")
            except:
                return False
            day_delta = today - fileStamp
            if(day_delta.days == 0):
                return True
            else:
                return False
        else:
            return False

    def secondary_loop(self):
        self.staticCounter += 1
        self.update_time_label()

        # Call this function again after 1 second
        self.root.after(1000, self.secondary_loop)

        if(self.staticCounter % 20 == 0):
            self.save_times()
        if(self.staticCounter % 1000 == 0):
            b.backup(recentSavePath,backupPath,5)
    # This method simply saves all times to the given folder.
    def save_times(self):
        rightNow = time.perf_counter()
        for runningClient in self.runningClients:
            self.loadedClients[runningClient]["saved_time"] += (rightNow - self.loadedClients[runningClient]["start_time"])
            self.loadedClients[runningClient]["start_time"] = rightNow

        runningString = datetime.datetime.strftime(datetime.datetime.now(),"%m/%d/%y")
        runningString += "\n\n"
        for client in self.loadedClients.keys():
            runningString += client + " | " + datetime.datetime.strftime(datetime.datetime.fromtimestamp(self.loadedClients[client]["saved_time"]), '%H:%M:%S') + "\n"

        saveFile = open(recentSavePath,"w")
        saveFile.writelines(runningString)
        saveFile.close()
    # This method updates the currently active time_label with the new time. Optionally, this method can
    # update all time labels.
    def update_time_label(self,updateAll=False):
        if(updateAll):
            clientList = self.loadedClients.keys()
        else:
            clientList = self.runningClients
        for client in clientList:
            if(self.loadedClients[client]["running"]):
                self.loadedClients[client]["time_label"].configure(text=datetime.datetime.strftime(datetime.datetime.fromtimestamp(self.loadedClients[client]["saved_time"] + (time.perf_counter() - self.loadedClients[client]["start_time"])), '%H:%M:%S'))
            else:
                self.loadedClients[client]["time_label"].configure(text=datetime.datetime.strftime(datetime.datetime.fromtimestamp(self.loadedClients[client]["saved_time"]), '%H:%M:%S'))
    # Main loop drives the actual program.
    def main_loop(self):
        secondaryThread = threading.Thread(self.secondary_loop())
        secondaryThread.start()

        self.root.mainloop()

app = ClientTracker()
app.main_loop()
