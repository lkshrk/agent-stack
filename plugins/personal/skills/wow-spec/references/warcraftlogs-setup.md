# Connect Warcraft Logs — one-time setup

Warcraft Logs requires an authenticated connection before the assistant can analyse its public rankings and logged talent builds. It calls this **OAuth**. Your part is to create two access values once and save them securely; the assistant handles the technical connection afterward.

This setup reads **public data**. You do not need to upload your own logs. It does not grant access to private reports. [Warcraft Logs documentation](https://www.warcraftlogs.com/api/docs)

If you would rather skip setup, ask **“Use Method's build instead.”** That gives you a published guide build rather than our own Warcraft Logs aggregation.

## 1. Open your Warcraft Logs account

Open [Warcraft Logs API Clients](https://www.warcraftlogs.com/api/clients/) in your browser and sign in. Create a Warcraft Logs account if you do not already have one.

Choose the option to create a new client, usually labelled **Create Client**. Here, “client” just means an entry that allows the assistant's local tool to connect.

## 2. Fill in the connection form

| Field | What to enter |
| --- | --- |
| Name | `WoW Spec Helper` — this is just a name you recognise |
| Redirect URI / URL, if required | `http://localhost` |
| Public Client, if shown | Leave it **unchecked**. The tool needs a client with a secret. |

Create/save the client. Keep the page open: you need its **Client ID** and **Client Secret** for the next step. If the secret is hidden, use the page's reveal/copy control.

The redirect address is a form value; you do not need to open it or run a website. This public-data connection uses the client-credentials method, which does not use a browser callback. [Authentication details](https://www.warcraftlogs.com/api/docs)

## 3. Save the two values in your password manager

**Do not paste the Client Secret into chat or send a screenshot containing it.** It is different from your Warcraft Logs account password.

If you already have a password manager connected to the assistant, use it. The steps below are for **Bitwarden**, whose local connection is supported by this skill. If you use another app or have no connection set up, tell the assistant its name first so it can check the available secure storage options. You do not need to install command-line tools just to follow this guide.

In Bitwarden:

1. Create a new **Login** item named **Warcraft Logs — WoW Spec**. Put it in a folder of your choice.
2. In the item's **Custom fields** section, add the following two fields. Copy the field names exactly:

| Custom field name | Field type | Value to paste from Warcraft Logs |
| --- | --- | --- |
| `client_id` | Text | Client ID |
| `secret_id` | Hidden | Client Secret |

3. Save the item and let your vault sync.

`secret_id` is the field name our helper reads; Warcraft Logs calls the value **Client Secret**. Do not put these values only in the normal username/password fields. [Bitwarden custom-field instructions](https://bitwarden.com/help/custom-fields/)

## 4. Tell the assistant where you saved it

Send the item name and folder, not their secret contents. For example:

> I saved the Warcraft Logs credentials in Bitwarden. The item is “Warcraft Logs — WoW Spec” in my “Gaming” folder. Use it to connect and test Warcraft Logs.

Replace “Gaming” with your actual folder, or say that the item has no folder. If your password manager asks you to unlock or sign in, do that in its own prompt.

## 5. Let the assistant test the connection

The assistant should confirm that it can read the two fields and retrieve public Warcraft Logs data, without displaying the values. A successful result should say **“Warcraft Logs connected; public data query passed.”**

Then ask normally, for example:

> Give me an Affliction Warlock M+ build.

You do not need to create another client or handle access tokens for each question.

## If something goes wrong

| What happens | Next step |
| --- | --- |
| The client page asks you to sign in | Sign in on Warcraft Logs, then reopen the client page. |
| You cannot find a Client Secret | Check whether you created a public client. Tell the assistant which field labels you see, without including values. |
| The assistant cannot find the saved item | Confirm its exact name/folder. The assistant should check its local vault sync before asking you to create another item. |
| The assistant cannot read your password manager | Tell it which password manager and device you use. A local connection may still need setup; saving the item alone does not establish that connection. |
| Warcraft Logs rejects the credentials | Check that both values came from the same client and were copied into the two correctly named fields. |
| Your vault needs authentication | Sign in/unlock through its own prompt. Never send your master password or verification code in chat. |

If the form has changed, tell the assistant what labels you see instead of guessing. The connection method is documented by Warcraft Logs; account-specific screens can vary.
