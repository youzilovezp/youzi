# Source: https://www.twilio.com/en-us/messaging/whatsapp
# Scrapers: firecrawl,crawl4ai,trafilatura,newspaper3k,readability,markdownify,playwright
# Time: 12.4s
# Success: True

```
// Download the helper library from https://www.twilio.com/docs/node/install
// Find your Account SID and Auth Token at twilio.com/console
// and set the environment variables. See http://twil.io/secure
const accountSid = process.env.TWILIO_ACCOUNT_SID;
const authToken = process.env.TWILIO_AUTH_TOKEN;
const client = require('twilio')(accountSid, authToken);
client.messages
  .create({
     body: 'This is a message that I want to send over WhatsApp with Twilio!',
     from: 'whatsapp:+14155238886',
     to: 'whatsapp:+15005550006'
   })
  .then(message => console.log(message.sid));
```
```
# Download the helper library from https://www.twilio.com/docs/python/install
import os
from twilio.rest import Client
# Find your Account SID and Auth Token at twilio.com/console
# and set the environment variables. See http://twil.io/secure
account_sid = os.environ['TWILIO_ACCOUNT_SID']
auth_token = os.environ['TWILIO_AUTH_TOKEN']
client = Client(account_sid, auth_token)
message = client.messages \\
    .create(
         body='This is a message that I want to send over WhatsApp with Twilio!',
         from_='whatsapp:+14155238886',
         to='whatsapp:+15005550006'
     )
print(message.sid)
```
```
// Install the C# / .NET helper library from twilio.com/docs/csharp/install
using System;
using Twilio;
using Twilio.Rest.Api.V2010.Account;
class Program
{
    static void Main(string[] args)
    {
        // Find your Account SID and Auth Token at twilio.com/console
        // and set the environment variables. See http://twil.io/secure
        string accountSid = Environment.GetEnvironmentVariable("TWILIO_ACCOUNT_SID");
        string authToken = Environment.GetEnvironmentVariable("TWILIO_AUTH_TOKEN");
        TwilioClient.Init(accountSid, authToken);
        var message = MessageResource.Create(
            body: "This is a message that I want to send over WhatsApp with Twilio!",
            from: new Twilio.Types.PhoneNumber("whatsapp:+14155238886"),

[ See all products  ](https://www.twilio.com/en-us/products)
See Twilio’s [latest innovations](https://www.twilio.com/en-us/products/beta) and [CDP integrations](https://www.twilio.com/en-us/catalog)
Solutions  An icon of a down chevron
Use Cases
An icon of a plus symbol
An icon of a minus symbol
  * [ Verification and identity An icon of a right chevron ](https://www.twilio.com/en-us/use-cases/user-verification-identity)
  * [ Fraud prevention ](https://www.twilio.com/en-us/use-cases/fraud-prevention)
  * [ Alerts and notifications An icon of a right chevron ](https://www.twilio.com/en-us/use-cases/alerts-and-notifications)
  * [ Appointment reminders ](https://www.twilio.com/en-us/use-cases/appointment-reminders)
  * [ Lead alerts ](https://www.twilio.com/en-us/use-cases/lead-alerts)
  * [ Mass texting ](https://www.twilio.com/en-us/use-cases/mass-texting)
  * [ Marketing and promotions An icon of a right chevron ](https://www.twilio.com/en-us/use-cases/marketing-and-promotions)
  * [ SMS marketing ](https://www.twilio.com/en-us/solutions/text-marketing)
  * [ Cross-sell and upsell ](https://www.twilio.com/en-us/use-cases/boost-cross-sell-upsell)
  * [ Optimize ad spend ](https://www.twilio.com/en-us/use-cases/optimize-ad-spend)
  * [ Support and sales An icon of a right chevron ](https://www.twilio.com/en-us/use-cases/support-and-sales)
  * [ Voice AI ](https://www.twilio.com/en-us/use-cases/voice-AI)
  * [ AI agent productivity ](https://www.twilio.com/en-us/solutions/agent-productivity)
  * [ IVR ](https://www.twilio.com/en-us/use-cases/ivr)
  * [ Contact center ](https://www.twilio.com/en-us/flex/use-cases/contact-center)
  * [ Customer data management An icon of a right chevron ](https://www.twilio.com/en-us/use-cases/customer-data-management-integration)

[ Communications  An icon of a right chevron ](https://www.twilio.com/en-us/cpaas)
An icon of a plus symbol
An icon of a minus symbol
  * [ Messaging An icon of a right chevron ](https://www.twilio.com/en-us/messaging)
  * [ SMS ](https://www.twilio.com/en-us/messaging/channels/sms)
  * [ WhatsApp ](https://www.twilio.com/en-us/messaging/channels/whatsapp)
  * [ RCS ](https://www.twilio.com/en-us/messaging/channels/rcs)
  * [ Voice An icon of a right chevron ](https://www.twilio.com/en-us/voice)
  * [ SIP Trunking ](https://www.twilio.com/en-us/sip-trunking)
  * [ Email An icon of a right chevron ](https://www.twilio.com/en-us/products/email-api)
  * [ SMTP Service ](https://www.twilio.com/en-us/products/email-api/smtp-service)
  * [ Phone Numbers An icon of a right chevron ](https://www.twilio.com/en-us/phone-numbers)
  * [ Toll-free ](https://www.twilio.com/en-us/phone-numbers/toll-free)
  * [ 10DLC ](https://www.twilio.com/en-us/phone-numbers/a2p-10dlc)
  * [ Short Codes ](https://www.twilio.com/en-us/messaging/channels/sms/short-codes)
  * [ Video API An icon of a right chevron ](https://www.twilio.com/en-us/video)
  * [ Flex An icon of a right chevron ](https://www.twilio.com/en-us/flex)
  * [ Communications  ](https://www.twilio.com/en-us/cpaas)

[ Conversations  An icon of a right chevron ](https://www.twilio.com/en-us/products/conversational-ai)
An icon of a plus symbol
An icon of a minus symbol
  * Twilio Conversation memory icon
[ Conversation Memory An icon of a right chevron ](https://www.twilio.com/en-us/products/conversational-ai/conversation-memory) New
Build a persistent memory of customer interactions
  * Twilio Conversation orchestrator icon
[ Conversation Orchestrator An icon of a right chevron ](https://www.twilio.com/en-us/products/conversational-ai/conversation-orchestrator) New
Keep conversations connected across channels
  * Twilio Conversation intelligence icon
[ Conversation Intelligence An icon of a right chevron ](https://www.twilio.com/en-us/products/conversational-ai/conversational-intelligence)
Extract context from real-time conversations
  * Twilio Conversation relay icon
[ Conversation Relay An icon of a right chevron ](https://www.twilio.com/en-us/products/conversational-ai/conversationrelay)
Build advanced voice AI for natural conversations
  * [ Conversations  ](https://www.twilio.com/en-us/products/conversational-ai)

Login  An icon of a down chevron
  * Log in to
    * [ Twilio Console  Conversations, Communications and Authentication ](https://www.twilio.com/login)
  * * * *
  * Or log in to access:
    * [ Twilio Segment Customer Data Platform ](https://app.segment.com/login?ext-anonymousId=8a62e9c8-7604-4510-ac19-2344b18bf923&ext-gaClientId=830653336.1777564134&ext-gaSessionId=1778069963&utm_referrer=https%3A%2F%2Fwww.twilio.com%2Fen-us&_gl=1*1i30f76*_gcl_au*OTQ3OTc1NTU4LjE3NzIyMjMxNTE.*_ga*ODMwNjUzMzM2LjE3Nzc1NjQxMzQ.*_ga_RRP8K4M4F3*czE3NzgwNjk5NjMkbzE4JGcxJHQxNzc4MDczMjIzJGoyOCRsMCRoMA)
    * [ Twilio SendGrid Email and Marketing Campaigns ](https://login.sendgrid.com/login/identifier?ext-anonymousId=8a62e9c8-7604-4510-ac19-2344b18bf923&ext-gaClientId=830653336.1777564134&ext-gaSessionId=1778069963&utm_referrer=https%3A%2F%2Fwww.twilio.com%2Fen-us&_gl=1*1u46np*_gcl_au*OTQ3OTc1NTU4LjE3NzIyMjMxNTE.*_ga*ODMwNjUzMzM2LjE3Nzc1NjQxMzQ.*_ga_RRP8K4M4F3*czE3NzgwNjk5NjMkbzE4JGcxJHQxNzc4MDczMDYyJGo1MCRsMCRoMA)

// Install the Java helper library from twilio.com/docs/java/install import com.twilio.Twilio; import com.twilio.rest.api.v2010.account.Message; import com.twilio.type.PhoneNumber; public class Example { // Find your Account SID and Auth Token at twilio.com/console // and set the environment variables. See http://twil.io/secure public static final String ACCOUNT_SID = System.getenv("TWILIO_ACCOUNT_SID"); public static final String AUTH_TOKEN = System.getenv("TWILIO_AUTH_TOKEN"); public static void main(String[] args) { Twilio.init(ACCOUNT_SID, AUTH_TOKEN); Message message = Message.creator( new com.twilio.type.PhoneNumber("whatsapp:+15005550006"), new com.twilio.type.PhoneNumber("whatsapp:+14155238886"), "This is a message that I want to send over WhatsApp with Twilio!") .create(); System.out.println(message

[... 内容已截断 ...]
